import { createBrowserRouter } from 'react-router-dom';
import { Layout } from './components/Layout';
import { DashboardPage } from './pages/DashboardPage';
import { DataCenterPage } from './pages/DataCenterPage';
import { AIAnalysisPage } from './pages/AIAnalysisPage';
import { SupportResistancePage } from './pages/SupportResistancePage';
import { PriceActionPage } from './pages/PriceActionPage';

export const router = createBrowserRouter(
  [
    {
      path: '/',
      element: <Layout />,
      children: [
        { index: true, element: <DashboardPage /> },
        { path: 'data', element: <DataCenterPage /> },
        { path: 'ai', element: <AIAnalysisPage /> },
        { path: 'support-resistance', element: <SupportResistancePage /> },
        { path: 'price-action', element: <PriceActionPage /> },
      ],
    },
  ],
  { basename: import.meta.env.BASE_URL },
);
