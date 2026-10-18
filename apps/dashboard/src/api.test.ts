import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  getHealth,
  getLiveEvents,
  getRunReplay,
  intervene,
} from './api'

const fetchMock = vi.fn()

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})

describe('dashboard API client', () => {
  it('requests health with same-origin credentials', async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          status: 'ok',
          version: '0.1.0',
          database: { status: 'ok', latency_ms: 2, error: null },
          redis: { status: 'ok', latency_ms: 1, error: null },
          timestamp: 1,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )

    await expect(getHealth()).resolves.toMatchObject({ status: 'ok' })
    expect(fetchMock).toHaveBeenCalledWith('/health', {
      credentials: 'include',
      headers: { Accept: 'application/json' },
    })
  })

  it('encodes replay identifiers and reports API errors safely', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'run not found' }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    await expect(getRunReplay('run/with spaces')).rejects.toEqual(
      expect.objectContaining<ApiError>({
        name: 'ApiError',
        status: 404,
        message: 'run not found',
      }),
    )
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/api/v1/dashboard/runs/run%2Fwith%20spaces/replay',
    )
  })

  it('posts intervention commands with a typed action payload', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ status: 'paused' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    await expect(intervene('run-123', 'pause')).resolves.toEqual({
      status: 'paused',
    })
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/dashboard/runs/run-123/intervention', {
      method: 'POST',
      credentials: 'include',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ action: 'pause' }),
    })
  })

  it('resynchronizes live events using an encoded cursor', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ events: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    await expect(getLiveEvents('2026-01-01T00:00:00+00:00')).resolves.toEqual({
      events: [],
    })
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/api/v1/dashboard/events?after=2026-01-01T00%3A00%3A00%2B00%3A00',
    )
  })
})
