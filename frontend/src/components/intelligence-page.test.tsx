import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { IntelligencePage } from './intelligence-page'

afterEach(() => vi.unstubAllGlobals())
it('requests a real weekly report and shows API errors instead of fabricated results', async () => {
  const fetch = vi.fn(async () => new Response(JSON.stringify({error: {message: '数据不可用', code: 'unavailable'}}), {status: 503}))
  vi.stubGlobal('fetch', fetch)
  render(<IntelligencePage token='demo-analyst'/>)
  await userEvent.click(screen.getByRole('button', {name: '生成并保存周报'}))
  expect(await screen.findByRole('alert')).toHaveTextContent('数据不可用')
  expect(fetch.mock.calls.length).toBe(1)
  expect(screen.queryByText('利润贡献桥')).not.toBeInTheDocument()
})
