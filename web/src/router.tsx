import { createBrowserRouter } from 'react-router-dom';
import { Layout } from './components/Layout';
import { DashboardPage } from './pages/DashboardPage';
import { StrategiesPage } from './pages/StrategiesPage';
import { StrategyDetailPage } from './pages/StrategyDetailPage';
import { ExperimentsPage } from './pages/ExperimentsPage';
import { ReplayWorkbenchPage } from './pages/ReplayWorkbenchPage';
import { DeploymentsPage } from './pages/DeploymentsPage';
import { RiskPage } from './pages/RiskPage';
import { DataCenterPage } from './pages/DataCenterPage';
import { NotificationsPage } from './pages/NotificationsPage';

export const router = createBrowserRouter(
  [
    {
      path: '/',
      element: <Layout />,
      children: [
        { index: true, element: <DashboardPage /> },
        { path: 'strategies', element: <StrategiesPage /> },
        { path: 'strategies/:id', element: <StrategyDetailPage /> },
        { path: 'experiments', element: <ExperimentsPage /> },
        { path: 'replay', element: <ReplayWorkbenchPage /> },
        { path: 'deployments', element: <DeploymentsPage /> },
        { path: 'risk', element: <RiskPage /> },
        { path: 'data', element: <DataCenterPage /> },
        { path: 'notifications', element: <NotificationsPage /> },
      ],
    },
  ],
  { basename: import.meta.env.BASE_URL },
);
