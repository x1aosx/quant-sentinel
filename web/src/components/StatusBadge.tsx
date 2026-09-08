export function StatusBadge({ status }: { status: string }) {
  const lower = status.toLowerCase();
  let cls = 'badge-neutral';
  if (['ok', 'running', 'succeeded', 'filled', 'delivered', 'approved', 'accepted', 'paper_verified'].some((s) => lower.includes(s))) {
    cls = 'badge-ok';
  } else if (['warn', 'queued', 'paused_entries', 'exit_only', 'watch', 'partial'].some((s) => lower.includes(s))) {
    cls = 'badge-warn';
  } else if (['failed', 'breach', 'error', 'rejected', 'inconclusive', 'invalid'].some((s) => lower.includes(s))) {
    cls = 'badge-danger';
  } else if (['specified', 'backtested', 'oos_passed', 'unit_verified'].some((s) => lower.includes(s))) {
    cls = 'badge-info';
  }
  return <span className={`badge ${cls}`}>{status}</span>;
}
