import { clsx } from 'clsx'

/** 拼接 className，过滤假值。 */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return clsx(parts)
}
