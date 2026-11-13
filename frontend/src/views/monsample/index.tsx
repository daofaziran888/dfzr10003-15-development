import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'
import SamplingTimeline from '@/components/SamplingTimeline'

const ENDPOINT = '/api/monsample'

type Row = Record<string, string | number | null>

const defaultForm = {
  指标编号: 'MONI-0002',
  采样批次: '',
  来源类型: '调度执行',
  来源编号: 'ETL-0002',
  指标值: '',
  采样状态: '成功',
  失败来源: '',
}

export default function MonsampleView() {
  const [rows, setRows] = useState<Row[]>([])
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState('')
  const [metric, setMetric] = useState('')
  const [form, setForm] = useState<Record<string, string>>(defaultForm)

  const reload = useCallback(async () => {
    try {
      const response = await request(`${ENDPOINT}?size=200`)
      if (!response.ok) {
        throw new Error('监控采样统计读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '监控采样统计读取失败')
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  const stats = useMemo(() => [
    { label: '采样读数总数', value: rows.length },
    { label: '待确认告警', value: rows.filter((row) => row['告警状态'] === '待确认').length },
    { label: '失败批次', value: rows.filter((row) => row['采样状态'] === '失败').length },
  ], [rows])

  async function submitCallback() {
    setErrorMessage('')
    setNotice('')
    const values: Record<string, string | number> = { ...form }
    if (form['指标值'].trim() !== '') {
      values['指标值'] = Number(form['指标值'])
    } else {
      delete values['指标值']
    }
    try {
      const response = await request(`${ENDPOINT}/callback`, {
        method: 'POST',
        body: JSON.stringify({ values }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) {
        throw new Error(payload?.message || '采样回调未生效，请检查必填字段')
      }
      setNotice(payload.message ?? '监控读数已生成')
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '采样回调失败')
    }
  }

  return (
    <section className="page" data-module="monsample">
      <header className="page-head">
        <div>
          <h2>监控采样时间线</h2>
          <p className="page-desc">值班人员可追溯每次采样的批次、来源、指标值与阈值版本；告警按待确认、已确认、已忽略流转，失败批次可继续重采。</p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出监控采样清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault() }}>
        <label className="filter-item">
          <span>采样批次</span>
          <input value={keyword} placeholder="按批次或采样编号检索" onChange={(event) => setKeyword(event.target.value)} />
        </label>
        <label className="filter-item">
          <span>指标编号</span>
          <input value={metric} placeholder="如 MONI-0002" onChange={(event) => setMetric(event.target.value)} />
        </label>
        <label className="filter-item">
          <span>告警状态</span>
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">全部</option>
            <option value="待确认">待确认</option>
            <option value="已确认">已确认</option>
            <option value="已忽略">已忽略</option>
            <option value="无告警">无告警</option>
          </select>
        </label>
        <button className="btn ghost" type="button" onClick={() => { setKeyword(''); setMetric(''); setStatus('') }}>重置条件</button>
      </form>

      <section className="callback-panel">
        <h3>模拟调度 / 质量回调</h3>
        <p className="page-desc">调度执行或质量校验完成后调用同一入口生成读数；同一批次重复提交只会保留一份记录（幂等）。</p>
        <div className="callback-form">
          <label className="filter-item">
            <span>指标编号</span>
            <input value={form['指标编号']} onChange={(event) => setForm((prev) => ({ ...prev, 指标编号: event.target.value }))} />
          </label>
          <label className="filter-item">
            <span>采样批次</span>
            <input value={form['采样批次']} placeholder="如 BATCH-20261113-002" onChange={(event) => setForm((prev) => ({ ...prev, 采样批次: event.target.value }))} />
          </label>
          <label className="filter-item">
            <span>来源类型</span>
            <select
              value={form['来源类型']}
              onChange={(event) => setForm((prev) => ({
                ...prev,
                来源类型: event.target.value,
                来源编号: event.target.value === '调度执行' ? 'ETL-0002' : 'QUAL-0002',
              }))}
            >
              <option value="调度执行">调度执行</option>
              <option value="质量校验">质量校验</option>
            </select>
          </label>
          <label className="filter-item">
            <span>来源编号</span>
            <input value={form['来源编号']} onChange={(event) => setForm((prev) => ({ ...prev, 来源编号: event.target.value }))} />
          </label>
          <label className="filter-item">
            <span>采样状态</span>
            <select value={form['采样状态']} onChange={(event) => setForm((prev) => ({ ...prev, 采样状态: event.target.value }))}>
              <option value="成功">成功</option>
              <option value="失败">失败</option>
            </select>
          </label>
          {form['采样状态'] === '成功' ? (
            <label className="filter-item">
              <span>指标值</span>
              <input value={form['指标值']} placeholder="本次采样的新读数" onChange={(event) => setForm((prev) => ({ ...prev, 指标值: event.target.value }))} />
            </label>
          ) : (
            <label className="filter-item">
              <span>失败来源</span>
              <input value={form['失败来源']} placeholder="如 QUAL-0002 校验超时" onChange={(event) => setForm((prev) => ({ ...prev, 失败来源: event.target.value }))} />
            </label>
          )}
          <button className="btn primary" type="button" onClick={() => void submitCallback()}>提交采样回调</button>
        </div>
      </section>

      <SamplingTimeline keyword={keyword} metric={metric} status={status} />

      <footer className="page-foot">
        {notice && <span className="ok-text">{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
