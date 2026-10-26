import assert from 'node:assert'
import { describe, it } from 'node:test'
import axios from 'axios'
import { getApiErrorMessage } from './errors.ts'

describe('getApiErrorMessage', () => {
  it('extracts string detail from Axios error', () => {
    const error = new axios.AxiosError('Request failed', 'ERR_BAD_REQUEST', undefined, undefined, {
      status: 400,
      statusText: 'Bad Request',
      headers: {},
      config: {} as any,
      data: { detail: 'Fondos insuficientes para esta operación.' },
    })
    assert.strictEqual(getApiErrorMessage(error), 'Fondos insuficientes para esta operación.')
  })

  it('extracts string message when detail is missing from Axios error', () => {
    const error = new axios.AxiosError('Request failed', 'ERR_BAD_REQUEST', undefined, undefined, {
      status: 400,
      statusText: 'Bad Request',
      headers: {},
      config: {} as any,
      data: { message: 'Operación no permitida.' },
    })
    assert.strictEqual(getApiErrorMessage(error), 'Operación no permitida.')
  })

  it('handles FastAPI 422 array of validation errors', () => {
    const error = new axios.AxiosError('Validation error', 'ERR_BAD_REQUEST', undefined, undefined, {
      status: 422,
      statusText: 'Unprocessable Entity',
      headers: {},
      config: {} as any,
      data: {
        detail: [
          { loc: ['body', 'legs', 0, 'odds'], msg: 'El momio debe ser diferente de cero' },
          { loc: ['body', 'bankroll'], msg: 'El bankroll debe ser positivo' },
        ],
      },
    })
    assert.strictEqual(
      getApiErrorMessage(error),
      'El momio debe ser diferente de cero; El bankroll debe ser positivo'
    )
  })

  it('serializes object detail when detail is an arbitrary JSON object', () => {
    const error = new axios.AxiosError('Error', 'ERR_BAD_REQUEST', undefined, undefined, {
      status: 400,
      statusText: 'Bad Request',
      headers: {},
      config: {} as any,
      data: { detail: { code: 'INVALID_LEG', info: 'Leg conflict detected' } },
    })
    assert.strictEqual(getApiErrorMessage(error), '{"code":"INVALID_LEG","info":"Leg conflict detected"}')
  })

  it('handles ECONNABORTED timeout error code', () => {
    const error = new axios.AxiosError('timeout of 5000ms exceeded', 'ECONNABORTED')
    assert.strictEqual(getApiErrorMessage(error), 'La API agotó el tiempo de espera.')
  })

  it('handles network error when no response was received', () => {
    const error = new axios.AxiosError('Network Error', 'ERR_NETWORK')
    assert.strictEqual(
      getApiErrorMessage(error),
      'No fue posible conectar con la API. Verifica que el backend esté disponible.'
    )
  })

  it('falls back to HTTP status when response data has no detail or message', () => {
    const error = new axios.AxiosError('Server Error', 'ERR_BAD_RESPONSE', undefined, undefined, {
      status: 502,
      statusText: 'Bad Gateway',
      headers: {},
      config: {} as any,
      data: {},
    })
    assert.strictEqual(getApiErrorMessage(error), 'La API respondió con estado 502.')
  })

  it('extracts message from standard Error instance', () => {
    const error = new Error('Error de validación local')
    assert.strictEqual(getApiErrorMessage(error), 'Error de validación local')
  })

  it('returns generic fallback for non-Error unknown values', () => {
    assert.strictEqual(getApiErrorMessage('raw string error'), 'Ocurrió un error inesperado al consultar la API.')
    assert.strictEqual(getApiErrorMessage(null), 'Ocurrió un error inesperado al consultar la API.')
    assert.strictEqual(getApiErrorMessage(undefined), 'Ocurrió un error inesperado al consultar la API.')
  })
})
