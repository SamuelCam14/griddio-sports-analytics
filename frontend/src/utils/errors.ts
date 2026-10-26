import axios from 'axios'

export function getApiErrorMessage(error: unknown): string {
  if (axios.isAxiosError<{ detail?: unknown; message?: string }>(error)) {
    const rawDetail = error.response?.data?.detail ?? error.response?.data?.message
    if (typeof rawDetail === 'string' && rawDetail.trim()) return rawDetail
    if (Array.isArray(rawDetail)) {
      const messages = rawDetail
        .map((item) =>
          typeof item === 'object' && item !== null
            ? (item as { msg?: string; message?: string }).msg || (item as { msg?: string; message?: string }).message || JSON.stringify(item)
            : String(item)
        )
        .filter(Boolean)
      if (messages.length > 0) return messages.join('; ')
    }
    if (typeof rawDetail === 'object' && rawDetail !== null) {
      try {
        return JSON.stringify(rawDetail)
      } catch {
        return 'Detalle de error no serializable.'
      }
    }
    if (error.code === 'ECONNABORTED') return 'La API agotó el tiempo de espera.'
    if (!error.response) return 'No fue posible conectar con la API. Verifica que el backend esté disponible.'
    return `La API respondió con estado ${error.response.status}.`
  }
  return error instanceof Error ? error.message : 'Ocurrió un error inesperado al consultar la API.'
}
