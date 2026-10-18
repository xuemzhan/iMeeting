/** 录音块本地持久化：服务端确认前不删除，断线/刷新后可补传。 */

interface StoredChunk {
  key: string
  meetingId: number
  seq: number
  data: ArrayBuffer
  createdAt: number
}

const DB_NAME = 'meeting-audio-buffer'
const STORE = 'chunks'
let dbPromise: Promise<IDBDatabase | null> | null = null

function openDb(): Promise<IDBDatabase | null> {
  if (dbPromise) return dbPromise
  dbPromise = new Promise((resolve) => {
    if (!('indexedDB' in window)) {
      resolve(null)
      return
    }
    const request = indexedDB.open(DB_NAME, 1)
    request.onupgradeneeded = () => {
      const db = request.result
      if (!db.objectStoreNames.contains(STORE)) {
        const store = db.createObjectStore(STORE, { keyPath: 'key' })
        store.createIndex('meetingId', 'meetingId', { unique: false })
      }
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => resolve(null)
  })
  return dbPromise
}

function keyOf(meetingId: number, seq: number): string {
  return `${meetingId}:${seq}`
}

export async function putChunk(
  meetingId: number,
  seq: number,
  data: ArrayBuffer,
): Promise<void> {
  const db = await openDb()
  if (!db) return
  await new Promise<void>((resolve) => {
    const tx = db.transaction(STORE, 'readwrite')
    tx.objectStore(STORE).put({
      key: keyOf(meetingId, seq),
      meetingId,
      seq,
      data,
      createdAt: Date.now(),
    } satisfies StoredChunk)
    tx.oncomplete = () => resolve()
    tx.onerror = () => resolve()
    tx.onabort = () => resolve()
  })
}

export async function deleteChunk(meetingId: number, seq: number): Promise<void> {
  const db = await openDb()
  if (!db) return
  await new Promise<void>((resolve) => {
    const tx = db.transaction(STORE, 'readwrite')
    tx.objectStore(STORE).delete(keyOf(meetingId, seq))
    tx.oncomplete = () => resolve()
    tx.onerror = () => resolve()
  })
}

export async function listChunks(meetingId: number): Promise<StoredChunk[]> {
  const db = await openDb()
  if (!db) return []
  return new Promise((resolve) => {
    const tx = db.transaction(STORE, 'readonly')
    const index = tx.objectStore(STORE).index('meetingId')
    const request = index.getAll(meetingId)
    request.onsuccess = () =>
      resolve((request.result as StoredChunk[]).sort((a, b) => a.seq - b.seq))
    request.onerror = () => resolve([])
  })
}

export async function clearMeeting(meetingId: number): Promise<void> {
  const chunks = await listChunks(meetingId)
  await Promise.all(chunks.map((chunk) => deleteChunk(meetingId, chunk.seq)))
}
