import { useState, type Dispatch, type SetStateAction } from 'react'

import { meetingApi } from '../api/client'
import { runExportJob } from '../lib/exportJob'
import { parseHistoryTags } from '../pages/historyTags'

interface Options {
  selected: Set<number>
  refresh: () => Promise<void>
  removeFromCaches: (ids: number[]) => void
  setOpNotice: Dispatch<SetStateAction<string | null>>
}

export function useHistoryBulkActions({
  selected,
  refresh,
  removeFromCaches,
  setOpNotice,
}: Options) {
  const [busy, setBusy] = useState(false)
  const [action, setAction] = useState<'none' | 'rename' | 'tag'>('none')
  const [renameFind, setRenameFind] = useState('')
  const [renameReplace, setRenameReplace] = useState('')
  const [renamePrefix, setRenamePrefix] = useState('')
  const [renameSuffix, setRenameSuffix] = useState('')
  const [tagInput, setTagInput] = useState('')
  const [exportFormat, setExportFormat] = useState('md')
  const [exportStatus, setExportStatus] = useState<string | null>(null)

  const handleBulkTrash = async () => {
    if (!selected.size) return
    if (!confirm(`将选中的 ${selected.size} 场会议移入回收站？`)) return
    setBusy(true)
    try {
      const result = await meetingApi.bulkRemove([...selected])
      removeFromCaches(result.deleted_ids)
      await refresh()
      setOpNotice(
        result.skipped.length > 0
          ? `已移入回收站 ${result.deleted} 场；${result.skipped.length} 场被跳过（正在录音、已删除或无权限）。`
          : `已移入回收站 ${result.deleted} 场。`,
      )
    } catch (e) {
      alert(e instanceof Error ? e.message : '操作失败')
    } finally {
      setBusy(false)
    }
  }

  const handleBulkRestore = async () => {
    if (!selected.size) return
    setBusy(true)
    try {
      await meetingApi.bulkRestore([...selected])
      await refresh()
    } catch (e) {
      alert(e instanceof Error ? e.message : '恢复失败')
    } finally {
      setBusy(false)
    }
  }

  const handleBulkPurge = async () => {
    if (!selected.size) return
    if (
      !confirm(
        `彻底删除选中的 ${selected.size} 场会议？转写、纪要、音频都会删除，且不可恢复。`,
      )
    ) {
      return
    }
    setBusy(true)
    try {
      await meetingApi.bulkPurge([...selected])
      await refresh()
    } catch (e) {
      alert(e instanceof Error ? e.message : '彻底删除失败')
    } finally {
      setBusy(false)
    }
  }

  const handleArchive = async (archived: boolean) => {
    if (!selected.size) return
    setBusy(true)
    try {
      await meetingApi.bulkUpdate([...selected], { archived })
      await refresh()
    } catch (e) {
      alert(e instanceof Error ? e.message : '操作失败')
    } finally {
      setBusy(false)
    }
  }

  const applyRename = async () => {
    if (!selected.size) return
    setBusy(true)
    try {
      await meetingApi.bulkRename([...selected], {
        find: renameFind,
        replace: renameReplace,
        prefix: renamePrefix,
        suffix: renameSuffix,
      })
      setAction('none')
      await refresh()
    } catch (e) {
      alert(e instanceof Error ? e.message : '重命名失败')
    } finally {
      setBusy(false)
    }
  }

  const applyTags = async () => {
    const tags = parseHistoryTags(tagInput)
    if (!selected.size || tags.length === 0) return
    setBusy(true)
    try {
      await meetingApi.bulkUpdate([...selected], { tagsAdd: tags })
      setTagInput('')
      setAction('none')
      await refresh()
    } catch (e) {
      alert(e instanceof Error ? e.message : '打标签失败')
    } finally {
      setBusy(false)
    }
  }

  const exportSelected = async () => {
    if (!selected.size) return
    setBusy(true)
    try {
      setExportStatus('正在创建批量导出…')
      await runExportJob({
        createJob: () =>
          meetingApi.createBulkExportJob([...selected], exportFormat),
        getJob: meetingApi.exportJob,
        downloadJob: meetingApi.downloadExportJob,
        fallbackFilename: () => `meetings-${Date.now()}.zip`,
        maxPolls: 180,
        onProgress: () => setExportStatus('正在生成批量导出…'),
      })
    } catch (e) {
      alert(e instanceof Error ? e.message : '导出失败')
    } finally {
      setExportStatus(null)
      setBusy(false)
    }
  }

  return {
    busy,
    action,
    setAction,
    renameFind,
    setRenameFind,
    renameReplace,
    setRenameReplace,
    renamePrefix,
    setRenamePrefix,
    renameSuffix,
    setRenameSuffix,
    tagInput,
    setTagInput,
    exportFormat,
    setExportFormat,
    exportStatus,
    handleBulkTrash,
    handleBulkRestore,
    handleBulkPurge,
    handleArchive,
    applyRename,
    applyTags,
    exportSelected,
  }
}
