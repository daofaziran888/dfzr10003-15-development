import { useCallback, useEffect, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/monsample'

type Row = Record<string, string | number | null>

interface Props {
  title?: string
  metric?: string
  rule?: string
  task?: string
  keyword?: string
  status?: string
}

/** 监控采样时间线：监控页、质量页、调度页共用，展示同一批采样批次与阈值版本。 */
export default function SamplingTimeline({ title = '监控采样时间线', metric, rule, task, keyword, status }: Props) {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [handler, setHandler] = useState('')

  const reload = useCallback(async () => {
    setErrorMessage('')
    const params = new URLSearchParams()
    if (metric) params.set('metric', metric)
    if (rule) params.set('rule', rule)
    if (task) params.set('task', task)
    if (keyword) params.set('keyword', keyword)
    if (status) params.set('status', status)
    params.set('size', '50')
    try {
      const response = await request(`${ENDPOINT}?${params.toString()}`)
      if (!response.ok) {
        throw new Error('监控采样时间线读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '监控采样时间线读取失败')
    }
  }, [metric, rule, task, keyword, status])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setNotice('')
    const values: Record<string, string> = { action }
    if (action !== '重采失败批次') {
      const name = handler.trim()
      if (!name) {
        setErrorMessage('请先在时间线右上角填写处理人，再确认或忽略告警')
        return
      }
      values['处理人'] = name
    }
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ values }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) {
        throw new Error(payload?.message || '监控采样动作未生效，请稍后重试')
      }
      setNotice(payload.message ?? '操作已生效')
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '监控采样操作失败')
    }
  }

  return (
    <section className="timeline-panel" data-component="sampling-timeline">
      <div className="timeline-head">
        <div>
          <h3>{title}</h3>
          <p className="page-desc">每次调度执行或质量校验完成后生成一条读数；同一批次重复回调只保留一份记录。</p>
        </div>
        <label className="filter-item">
          <span>处理人（确认/忽略必填）</span>
          <input
            value={handler}
            placeholder="填写处理人姓名"
            onChange={(event) => setHandler(event.target.value)}
          />
        </label>
      </div>

      <ol className="timeline">
        {rows.map((row) => {
          const failed = row['采样状态'] === '失败'
          const breach = row['越界结果'] === '越界'
          const itemClass = failed ? 'timeline-item failed' : breach ? 'timeline-item breach' : 'timeline-item'
          return (
            <li key={String(row.id)} className={itemClass}>
              <div className="timeline-time">{row['采样时间'] ?? '—'}</div>
              <div className="timeline-body">
                <div>
                  <strong>{row['采样编号']}</strong>
                  <span> · 批次 {row['采样批次']} · {row['来源类型']} {row['来源编号']}</span>
                </div>
                <div>
                  指标 {row['指标编号']}（{row['监控对象'] ?? '—'}）· 采样周期 {row['采样周期'] ?? '—'}
                  · 调度任务 {row['调度任务'] || '—'} · 质量规则 {row['质量规则'] || '—'}
                </div>
                <div>
                  指标值 {row['指标值'] ?? '—'} · 阈值版本 {row['阈值版本'] ?? '—'}
                  （上界 {row['阈值上界'] ?? '—'} / 下界 {row['阈值下界'] ?? '—'}）
                </div>
                {row['失败来源'] ? <div className="error-text">失败来源：{row['失败来源']}</div> : null}
                <div>
                  <span className={`badge ${breach ? 'alert' : failed ? 'alert' : 'ok'}`}>{row['越界结果'] ?? '—'}</span>
                  <span className={`badge ${row['告警状态'] === '待确认' ? 'alert' : ''}`}>{row['告警状态'] ?? '—'}</span>
                  {row['处理人'] ? <span className="timeline-handler">处理人 {row['处理人']} · {row['处理时间']}</span> : null}
                </div>
                <div className="row-actions">
                  {row['告警状态'] === '待确认' && (
                    <button className="link" type="button" onClick={() => void runAction('确认告警', row)}>确认告警</button>
                  )}
                  {row['告警状态'] === '待确认' && (
                    <button className="link" type="button" onClick={() => void runAction('忽略告警', row)}>忽略告警</button>
                  )}
                  {failed && (
                    <button className="link" type="button" onClick={() => void runAction('重采失败批次', row)}>从失败批次重采</button>
                  )}
                </div>
              </div>
            </li>
          )
        })}
        {rows.length === 0 && <li className="empty-state">暂无监控采样读数，等待调度或质量校验回调</li>}
      </ol>

      <footer className="page-foot">
        <span>共 {total} 条监控采样读数</span>
        {notice && <span className="ok-text">{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
