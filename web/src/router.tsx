import { Navigate, createBrowserRouter } from 'react-router-dom';
import { Layout } from './components/Layout';
import { AIAnalysisPage } from './pages/AIAnalysisPage';
import { DashboardPage } from './pages/DashboardPage';
import { DataCenterPage } from './pages/DataCenterPage';
import { DiscoveryPage } from './pages/DiscoveryPage';
import { IntelligencePage } from './pages/IntelligencePage';
import { MarketAnalysisPage } from './pages/MarketAnalysisPage';
import { SchedulerPage } from './pages/SchedulerPage';
import { StockAnalysisWorkbenchPage } from './pages/StockAnalysisWorkbenchPage';
import { SystemConfigPage } from './pages/SystemConfigPage';

export const router = createBrowserRouter(
  [
    {
      path: '/',
      element: <Layout />,
      children: [
        { index: true, element: <DashboardPage /> },
        { path: 'data', element: <DataCenterPage /> },
        { path: 'intelligence', element: <IntelligencePage /> },
        { path: 'discovery', element: <DiscoveryPage /> },
        { path: 'analysis', element: <StockAnalysisWorkbenchPage /> },
        { path: 'ai', element: <AIAnalysisPage /> },
        { path: 'market-analysis', element: <MarketAnalysisPage /> },
        { path: 'scheduler', element: <SchedulerPage /> },
        { path: 'support-resistance', element: <Navigate to="/market-analysis" replace /> },
        { path: 'price-action', element: <Navigate to="/market-analysis" replace /> },
        { path: 'settings', element: <SystemConfigPage /> },
      ],
    },
  ],
  { basename: import.meta.env.BASE_URL },
);
