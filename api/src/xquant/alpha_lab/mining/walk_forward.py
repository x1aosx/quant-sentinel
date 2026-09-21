from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from ..config import MiningSettings
from ..data import BarFrame


@dataclass(frozen=True)
class WalkForwardFold:
    index: int
    train_start: int
    train_end: int
    gap: int
    validation_start: int
    validation_end: int

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("fold index cannot be negative")
        if self.train_start < 0 or self.train_end <= self.train_start:
            raise ValueError("fold train interval is invalid")
        if self.gap < 0:
            raise ValueError("purge gap cannot be negative")
        if self.validation_start != self.train_end + self.gap:
            raise ValueError("validation must begin exactly after the purge gap")
        if self.validation_end <= self.validation_start:
            raise ValueError("fold validation interval is invalid")

    @property
    def val_start(self) -> int:
        return self.validation_start

    @property
    def val_end(self) -> int:
        return self.validation_end

    @property
    def train_slice(self) -> slice:
        return slice(self.train_start, self.train_end)

    @property
    def validation_slice(self) -> slice:
        return slice(self.validation_start, self.validation_end)

    def to_dict(self) -> dict[str, int]:
        return {
            "index": self.index,
            "train_start": self.train_start,
            "train_end": self.train_end,
            "gap": self.gap,
            "validation_start": self.validation_start,
            "validation_end": self.validation_end,
        }


@dataclass(frozen=True)
class WalkForwardPlan:
    n_bars: int
    folds: tuple[WalkForwardFold, ...]
    holdout_start: int
    holdout_end: int

    def __post_init__(self) -> None:
        if self.n_bars < 1 or self.holdout_end != self.n_bars:
            raise ValueError("walk-forward plan must cover the complete sample")
        if self.holdout_start >= self.holdout_end:
            raise ValueError("the final holdout cannot be empty")
        previous_validation_end = -1
        for expected_index, fold in enumerate(self.folds):
            if fold.index != expected_index:
                raise ValueError("fold indices must be contiguous")
            if fold.validation_end > self.holdout_start:
                raise ValueError("a validation fold overlaps the final holdout")
            if fold.validation_start < previous_validation_end:
                raise ValueError("validation folds overlap or move backwards in time")
            previous_validation_end = fold.validation_end

    @property
    def holdout_slice(self) -> slice:
        return slice(self.holdout_start, self.holdout_end)

    @property
    def final_holdout(self) -> tuple[int, int]:
        return self.holdout_start, self.holdout_end

    def __iter__(self) -> Iterator[WalkForwardFold]:
        return iter(self.folds)

    def split(self, frame: BarFrame, fold: WalkForwardFold) -> tuple[BarFrame, BarFrame]:
        return (
            frame.slice_time(fold.train_start, fold.train_end),
            frame.slice_time(fold.validation_start, fold.validation_end),
        )

    def train_frame(self, frame: BarFrame, fold: WalkForwardFold) -> BarFrame:
        return frame.slice_time(fold.train_start, fold.train_end)

    def validation_frame(self, frame: BarFrame, fold: WalkForwardFold) -> BarFrame:
        return frame.slice_time(fold.validation_start, fold.validation_end)

    def holdout_frame(self, frame: BarFrame) -> BarFrame:
        return frame.slice_time(self.holdout_start, self.holdout_end)

    def to_dict(self) -> dict[str, object]:
        return {
            "n_bars": self.n_bars,
            "folds": [fold.to_dict() for fold in self.folds],
            "holdout_start": self.holdout_start,
            "holdout_end": self.holdout_end,
        }

    @classmethod
    def build(
        cls,
        n_bars: int,
        *,
        train_bars: int,
        validation_bars: int,
        gap: int,
        folds: int,
        holdout_bars: int,
    ) -> WalkForwardPlan:
        if n_bars < 1:
            raise ValueError("n_bars must be positive")
        if train_bars < 1 or validation_bars < 1:
            raise ValueError("train_bars and validation_bars must be positive")
        if folds < 1:
            raise ValueError("folds must be positive")
        if gap < 0:
            raise ValueError("gap cannot be negative")
        if holdout_bars < 1:
            raise ValueError("holdout_bars must be positive")
        holdout_start = n_bars - holdout_bars
        if holdout_start <= 0:
            raise ValueError("sample is too short for a final holdout")

        result: list[WalkForwardFold] = []
        train_end = train_bars
        for index in range(folds):
            validation_start = train_end + gap
            validation_end = validation_start + validation_bars
            if validation_end > holdout_start:
                break
            result.append(
                WalkForwardFold(
                    index=index,
                    train_start=train_end - train_bars,
                    train_end=train_end,
                    gap=gap,
                    validation_start=validation_start,
                    validation_end=validation_end,
                )
            )
            train_end += validation_bars
        if len(result) != folds:
            raise ValueError("sample is too short for the requested walk-forward folds and holdout")
        return cls(
            n_bars=n_bars,
            folds=tuple(result),
            holdout_start=holdout_start,
            holdout_end=n_bars,
        )

    @classmethod
    def from_settings(
        cls,
        n_bars: int,
        settings: MiningSettings,
    ) -> WalkForwardPlan:
        return cls.build(
            n_bars,
            train_bars=settings.walk_forward_train_bars,
            validation_bars=settings.walk_forward_validation_bars,
            gap=settings.walk_forward_gap_bars,
            folds=settings.walk_forward_folds,
            holdout_bars=settings.holdout_bars,
        )

    @classmethod
    def adaptive(
        cls,
        n_bars: int,
        settings: MiningSettings,
    ) -> WalkForwardPlan:
        """自适应构建 walk-forward 计划：数据不足时逐步降折、缩短训练窗。

        训练引擎（MiningEngine）与训练前置校验（TrainingManager）共用此实现，
        避免两处对「多少 bar 才够训练」的判断随时间漂移。

        数据过短时抛 ValueError（调用方按语义转成 InsufficientDataError 或直接失败）。
        """
        holdout = min(settings.holdout_bars, max(2, n_bars // 5))
        development = n_bars - holdout
        if development < 4:
            raise ValueError("bar frame is too short to create training folds")
        gap = min(settings.walk_forward_gap_bars, max(0, development // 50))
        requested_folds = min(settings.walk_forward_folds, max(1, development // 4))
        for folds in range(requested_folds, 0, -1):
            validation = min(
                settings.walk_forward_validation_bars,
                max(2, (development - gap - 2) // folds),
            )
            available_train = development - gap - validation * folds
            if available_train < 2:
                continue
            train = min(settings.walk_forward_train_bars, available_train)
            return cls.build(
                n_bars,
                train_bars=train,
                validation_bars=validation,
                gap=gap,
                folds=folds,
                holdout_bars=holdout,
            )
        raise ValueError("bar frame is too short for walk-forward validation")


def build_walk_forward_plan(
    n_bars: int,
    *,
    train_bars: int,
    validation_bars: int,
    gap: int,
    folds: int,
    holdout_bars: int,
) -> WalkForwardPlan:
    return WalkForwardPlan.build(
        n_bars,
        train_bars=train_bars,
        validation_bars=validation_bars,
        gap=gap,
        folds=folds,
        holdout_bars=holdout_bars,
    )


__all__ = ["WalkForwardFold", "WalkForwardPlan", "build_walk_forward_plan"]
