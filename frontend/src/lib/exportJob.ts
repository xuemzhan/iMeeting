import type { Job } from '../api/types'

type SaveBlob = (blob: Blob, filename: string) => void

export interface RunExportJobOptions {
  /** 创建导出任务。 */
  createJob: () => Promise<Job>
  /** 查询任务状态。 */
  getJob: (jobId: number) => Promise<Job>
  /** 下载已完成的任务产物。 */
  downloadJob: (jobId: number) => Promise<Blob>
  /** 服务端未提供文件名时使用的回退名称。 */
  fallbackFilename: string | (() => string)
  timeoutMessage?: string
  maxPolls?: number
  pollIntervalMs?: number
  onProgress?: (job: Job) => void
  /** 测试注入点；生产环境使用浏览器下载。 */
  save?: SaveBlob
  /** 测试注入点；生产环境使用定时器。 */
  sleep?: (milliseconds: number) => Promise<void>
}

function saveBlobToDisk(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

/**
 * 等待导出任务完成并保存产物。
 *
 * 导出任务接口虽然已有统一结构，但详情页和批量导出需要不同的进度文案、
 * 轮询上限与下载文件名。这里保留这些差异，只收敛重复的任务等待流程。
 */
export async function runExportJob({
  createJob,
  getJob,
  downloadJob,
  fallbackFilename,
  timeoutMessage = '导出超时',
  maxPolls = 120,
  pollIntervalMs = 1000,
  onProgress,
  save = saveBlobToDisk,
  sleep = (milliseconds) =>
    new Promise((resolve) => window.setTimeout(resolve, milliseconds)),
}: RunExportJobOptions): Promise<Job> {
  let job = await createJob()

  for (let index = 0; index < maxPolls; index++) {
    if (job.status === 'failed') {
      throw new Error(job.last_error || '导出失败')
    }
    if (job.status === 'succeeded') break

    onProgress?.(job)
    await sleep(pollIntervalMs)
    job = await getJob(job.id)
  }

  if (job.status !== 'succeeded') {
    throw new Error(timeoutMessage)
  }

  const blob = await downloadJob(job.id)
  const filename =
    job.download_name ||
    (typeof fallbackFilename === 'function'
      ? fallbackFilename()
      : fallbackFilename)
  save(blob, filename)
  return job
}
