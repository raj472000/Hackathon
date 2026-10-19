import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Link, Route, Routes } from 'react-router-dom'
import { ApiError } from './api/client'
import { Layout } from './components/Layout'
import { EmptyState } from './components/ui'
import { Concalls } from './pages/Concalls'
import { Instrument } from './pages/Instrument'
import { Overview } from './pages/Overview'
import { Research } from './pages/Research'
import { Rotation } from './pages/Rotation'
import { Screener } from './pages/Screener'
import { Status } from './pages/Status'
import { ThemeDetail, Themes } from './pages/Themes'
import { Watchlists } from './pages/Watchlists'

const client = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: (count, err) => !(err instanceof ApiError && err.status >= 400 && err.status < 500) && count < 2,
    },
  },
})

export default function App() {
  return (
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Overview />} />
            <Route path="screener" element={<Screener />} />
            <Route path="themes" element={<Themes />} />
            <Route path="themes/:id" element={<ThemeDetail />} />
            <Route path="instrument/:ref" element={<Instrument />} />
            <Route path="research" element={<Research />} />
            <Route path="research/:ref" element={<Research />} />
            <Route path="rotation" element={<Rotation />} />
            <Route path="concalls" element={<Concalls />} />
            <Route path="concalls/:ref" element={<Concalls />} />
            <Route path="watchlists" element={<Watchlists />} />
            <Route path="status" element={<Status />} />
            <Route path="*" element={<EmptyState title="This page does not exist" action={<Link to="/">Go to the overview</Link>} />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
