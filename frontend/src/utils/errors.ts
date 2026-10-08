import axios from 'axios'

export function getApiErrorMessage(error: unknown): string {
  if (axios.isAxiosError<{ detail?: string; message?: string }>(error)) {
    const detail = error.response?.data?.detail ?? error.response?.data?.message
    if (detail) return detail
    if (error.code === 'ECONNABORTED') return 'La API agotó el tiempo de espera.'
    if (!error.response) return 'No fue posible conectar con la API. Verifica que el backend esté disponible.'
    return `La API respondió con estado ${error.response.status}.`
  }
  return error instanceof Error ? error.message : 'Ocurrió un error inesperado al consultar la API.'
}
