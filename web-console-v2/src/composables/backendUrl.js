function trimTrailingSlash(value) {
  return typeof value === 'string' ? value.replace(/\/+$/, '') : ''
}

export function resolveBackendHttpBaseUrl(
  locationLike = window.location,
  env = import.meta.env,
) {
  const explicit = trimTrailingSlash(env?.VITE_BACKEND_HTTP_URL)
  if (explicit) return explicit

  const protocol = locationLike?.protocol || 'http:'
  const hostname = locationLike?.hostname || 'localhost'
  const port = locationLike?.port || ''

  if (env?.DEV && port && port !== '8765') {
    return `${protocol}//${hostname}:8765`
  }

  const suffix = port ? `:${port}` : ''
  return `${protocol}//${hostname}${suffix}`
}
