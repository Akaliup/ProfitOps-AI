import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { MessageSquare, Send, LoaderCircle } from 'lucide-react'
import { request } from '../api'
import type { WeeklyChatHistory, WeeklyChatMessage } from '../types'
import { Button } from './ui/button'
import { Card, CardContent, CardHeader, CardTitle } from './ui/card'

const statuses = {llm_success: '证据已核验', deterministic_fallback: '确定性降级', refused: '超范围或证据不足'}
const reasons: Record<string, string> = {
  not_configured: '未配置模型', unknown_evidence: '引用无法核对', unverified_number: '数字无法核对',
  invalid_llm_output: '回答格式或工具结果无效', out_of_scope: '超出快照范围',
  llm_tool_result_too_large: '快照证据超过模型读取限制', llm_tool_scope_mismatch: '工具越过周报范围',
}

export function WeeklyChat({reportId, token, onCitation}: {reportId: string; token: string; onCitation: (id: string) => void}) {
  const [items, setItems] = useState<WeeklyChatMessage[]>([])
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState('')
  const path = `/api/weekly-reports/${reportId}/messages`
  useEffect(() => {
    let active = true
    setLoaded(false)
    request<WeeklyChatHistory>(path, token).then(data => {if (active) {setItems(data.items); setLoaded(true)}})
      .catch(e => {if (active) setError(e instanceof Error ? e.message : '读取追问失败')})
    return () => {active = false}
  }, [path, token])
  async function send() {
    if (!question.trim() || busy) return
    setBusy(true); setError('')
    try {
      await request<WeeklyChatMessage>(path, token, {method: 'POST', body: JSON.stringify({question})})
      setQuestion('')
      // Recover canonical persisted user/assistant IDs and ordering, including refusals.
      const history = await request<WeeklyChatHistory>(path, token)
      setItems(history.items)
    } catch (e) {setError(e instanceof Error ? e.message : '追问失败')}
    finally {setBusy(false)}
  }
  return <motion.section layout initial={{opacity: 0, y: 12}} animate={{opacity: 1, y: 0}} exit={{opacity: 0, y: -8}}>
    <Card><CardHeader><CardTitle className='flex items-center gap-2'><MessageSquare size={20}/>追问这份周报</CardTitle></CardHeader>
      <CardContent className='space-y-4'>
        <p className='text-sm text-muted-foreground'>回答仅依据这份已保存快照 点击引用可查看证据</p>
        <div aria-live='polite' className='space-y-3'><AnimatePresence initial={false}>
          {items.map(item => <motion.article key={item.id} layout initial={{opacity: 0, y: 8}} animate={{opacity: 1, y: 0}} exit={{opacity: 0, y: -8}}
            className={`rounded-xl border p-4 ${item.role === 'user' ? 'bg-blue-50' : 'bg-white'}`}>
            <p className='mb-2 text-sm text-muted-foreground'>{item.role === 'user' ? '你的问题' : item.status && statuses[item.status]}{item.reason && ` · ${reasons[item.reason] ?? item.reason}`}</p>
            <p>{item.content}</p>
            {item.claims.map((claim, index) => <div key={index} className='mt-3'><p>{claim.explanation}</p>
              <div className='flex flex-wrap gap-2'>{claim.evidence_ids.map(id => <Button key={id} size='sm' variant='outline' onClick={() => onCitation(id)} aria-label={`查看证据 ${id}`}>{id}</Button>)}</div>
            </div>)}
          </motion.article>)}
        </AnimatePresence></div>
        {error && <motion.p initial={{opacity: 0}} animate={{opacity: 1}} role='alert'>{error}</motion.p>}
        <label className='block'>周报问题<textarea aria-label='周报问题' className='mt-2 w-full rounded-lg border p-3 transition-shadow focus:ring-2 focus:ring-blue-500'
          maxLength={1000} value={question} onChange={e => setQuestion(e.target.value)} disabled={busy || !loaded || items.length >= 20}/></label>
        <Button onClick={send} disabled={busy || !loaded || !question.trim() || items.length >= 20}>{busy ? <LoaderCircle className='animate-spin'/> : <Send size={16}/>}发送追问</Button>
        {items.length >= 20 && <p role='status'>这份周报已达到追问上限 历史消息仍可查看</p>}
      </CardContent>
    </Card>
  </motion.section>
}
