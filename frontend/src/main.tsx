import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { DashboardPage } from './pages/DashboardPage'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 2,
      staleTime: 60 * 1000,
    },
  },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          {/* Fase 2: <Route path="/analytics" element={<AnalyticsPage />} /> */}
          {/* Fase 2: <Route path="/parlay" element={<ParlayBuilderPage />} /> */}
          {/* Fase 3: <Route path="/cards" element={<CardsPage />} /> */}
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
)
