// ProtectedRoute.tsx: Checks authentication and route access before rendering protected content.
import React, { useEffect, useMemo, useState } from 'react'
import { Navigate, Outlet, useLocation } from 'react-router-dom'
import axios from 'axios'
import ErrorBoundary from './ErrorComponent'

// Describes the route scope data expected by this file.
interface RouteScope {
  section: "admin" | "student"
  schoolId: string | null
  classId: string | null
}

const ACCESS_CHECK_MESSAGE_DELAY_MS = 350
const ACCESS_CACHE_TTL_MS = 5 * 60 * 1000

const successfulAccessCache = new Map<string, number>()
const pendingAccessChecks = new Map<string, Promise<void>>()

// Reads the stored login token and rejects placeholder or missing values.
const getValidStoredToken = (): string | null => {
  const token = localStorage.getItem("AUTOTA_AUTH_TOKEN")

  if (!token) {
    return null
  }

  const cleanedToken = token.trim()

  if (
    !cleanedToken ||
    cleanedToken.toLowerCase() === "null" ||
    cleanedToken.toLowerCase() === "undefined"
  ) {
    localStorage.removeItem("AUTOTA_AUTH_TOKEN")
    return null
  }

  return cleanedToken
}

// Clears stored auth for this view.
const clearStoredAuth = (expectedToken?: string) => {
  if (expectedToken && getValidStoredToken() !== expectedToken) return
  localStorage.removeItem("AUTOTA_AUTH_TOKEN")
  successfulAccessCache.clear()
}

// Extracts the role, school ID, and optional class ID from a scoped route.
const getRouteScope = (pathname: string): RouteScope | null => {
  const match = pathname.match(/^\/(admin|student)\/school\/(\d+)(?:\/class\/(\d+))?(?:\/|$)/)

  if (!match) {
    return null
  }

  return {
    section: match[1] as "admin" | "student",
    schoolId: match[2] || null,
    classId: match[3] || null
  }
}

// Builds a cache key for the current access scope.
const getAccessCacheKey = (pathname: string): string | null => {
  const scope = getRouteScope(pathname)

  if (!scope || !scope.schoolId) {
    return null
  }

  return `${scope.section}:${scope.schoolId}:${scope.classId || "school"}`
}

// Combines the login token and route scope so cached access belongs to the current session.
const getSessionAccessCacheKey = (token: string | null, accessCacheKey: string | null): string | null => {
  if (!token || !accessCacheKey) {
    return null
  }

  return `${token}:${accessCacheKey}`
}

// Reuses a recent successful access check and discards entries older than the cache lifetime.
const hasFreshCachedAccess = (sessionAccessCacheKey: string | null): boolean => {
  if (!sessionAccessCacheKey) {
    return false
  }

  const cachedAt = successfulAccessCache.get(sessionAccessCacheKey)

  if (!cachedAt) {
    return false
  }

  if (Date.now() - cachedAt > ACCESS_CACHE_TTL_MS) {
    successfulAccessCache.delete(sessionAccessCacheKey)
    return false
  }

  return true
}

// Checks authentication and route access before rendering protected content.
const ProtectedRoute = ({ children }: { children?: React.ReactNode }) => {
  const location = useLocation()
  const token = getValidStoredToken()
  // Recomputes access cache key only when its dependencies change.
  const accessCacheKey = useMemo(() => getAccessCacheKey(location.pathname), [location.pathname])
  // Recomputes session access cache key only when its dependencies change.
  const sessionAccessCacheKey = useMemo(
    () => getSessionAccessCacheKey(token, accessCacheKey),
    [token, accessCacheKey]
  )

  const startsWithCachedAccess = hasFreshCachedAccess(sessionAccessCacheKey)

  // Keeps the values that drive this component’s display and user interactions in React state.
  const [isCheckingAccess, setIsCheckingAccess] = useState(Boolean(token && accessCacheKey && !startsWithCachedAccess))
  const [showAccessMessage, setShowAccessMessage] = useState(false)
  const [checkedAccessKey, setCheckedAccessKey] = useState<string | null>(startsWithCachedAccess ? sessionAccessCacheKey : null)
  const [hasAccess, setHasAccess] = useState(!token ? false : startsWithCachedAccess || !accessCacheKey)
  const [kickoutPath, setKickoutPath] = useState("/login")

  // Synchronizes this component with the values listed in the dependency array.
  useEffect(() => {
    if (!isCheckingAccess) {
      setShowAccessMessage(false)
      return
    }

    setShowAccessMessage(false)

    const messageDelay = window.setTimeout(() => {
      setShowAccessMessage(true)
    }, ACCESS_CHECK_MESSAGE_DELAY_MS)

    return () => {
      window.clearTimeout(messageDelay)
    }
  }, [isCheckingAccess, accessCacheKey])

  // Loads or refreshes view data when the dependencies below change.
  useEffect(() => {
    let isMounted = true

    // Helper for check access used by this component.
    const checkAccess = async () => {
      if (!token) {
        clearStoredAuth()

        if (isMounted) {
          setKickoutPath("/login")
          setHasAccess(false)
          setCheckedAccessKey(null)
          setIsCheckingAccess(false)
        }

        return
      }

      const scope = getRouteScope(location.pathname)

      if (!scope || !scope.schoolId || !accessCacheKey) {
        if (isMounted) {
          setHasAccess(true)
          setCheckedAccessKey(sessionAccessCacheKey)
          setIsCheckingAccess(false)
        }

        return
      }

      if (hasFreshCachedAccess(sessionAccessCacheKey)) {
        if (isMounted) {
          setHasAccess(true)
          setCheckedAccessKey(sessionAccessCacheKey)
          setIsCheckingAccess(false)
        }

        return
      }

      if (isMounted) {
        setIsCheckingAccess(true)
      }

      try {
        const headers = {
          Authorization: `Bearer ${token}`
        }
        const roleContext = encodeURIComponent(scope.section)

        // Helper for access request used by this component.
        const accessRequest = async () => {
          if (scope.classId) {
            await axios.get(
              `${import.meta.env.VITE_API_URL}/classes/validate_class_access/${scope.classId}?school_id=${scope.schoolId}&role_context=${roleContext}`,
              { headers }
            )
          } else {
            await axios.get(
              `${import.meta.env.VITE_API_URL}/classes/get_classes_and_ids?school_id=${scope.schoolId}&role_context=${roleContext}`,
              { headers }
            )
          }

          if (sessionAccessCacheKey) {
            const now = Date.now()
            for (const [key, cachedAt] of successfulAccessCache) {
              if (now - cachedAt > ACCESS_CACHE_TTL_MS) successfulAccessCache.delete(key)
            }
            if (successfulAccessCache.size >= 200) successfulAccessCache.clear()
            successfulAccessCache.set(sessionAccessCacheKey, now)
          }
        }

        const pendingCheck = sessionAccessCacheKey ? pendingAccessChecks.get(sessionAccessCacheKey) : null

        if (pendingCheck) {
          await pendingCheck
        } else {
          const newPendingCheck = accessRequest()

          if (sessionAccessCacheKey) {
            pendingAccessChecks.set(sessionAccessCacheKey, newPendingCheck)
          }

          try {
            await newPendingCheck
          } finally {
            if (sessionAccessCacheKey && pendingAccessChecks.get(sessionAccessCacheKey) === newPendingCheck) {
              pendingAccessChecks.delete(sessionAccessCacheKey)
            }
          }
        }

        if (isMounted) {
          setHasAccess(true)
          setCheckedAccessKey(sessionAccessCacheKey)
          setIsCheckingAccess(false)
        }
      } catch (err: any) {
        if (!isMounted) return
        if (err?.response?.status === 401 || err?.response?.status === 422) {
          clearStoredAuth(token)

          if (isMounted) {
            setKickoutPath("/login")
            setHasAccess(false)
            setCheckedAccessKey(sessionAccessCacheKey)
            setIsCheckingAccess(false)
          }

          return
        }

        if (isMounted) {
          setKickoutPath("/schools")
          setHasAccess(false)
          setCheckedAccessKey(sessionAccessCacheKey)
          setIsCheckingAccess(false)
        }
      }
    }

    checkAccess()

    return () => {
      isMounted = false
    }
  }, [accessCacheKey, location.pathname, sessionAccessCacheKey, token])

  if (!token) {
    clearStoredAuth()
    // Renders the interface using the current data and interaction state.
    return <Navigate to="/login" replace />
  }

  if (isCheckingAccess || (accessCacheKey && checkedAccessKey !== sessionAccessCacheKey)) {
    if (!showAccessMessage) {
      return null
    }

    // Renders the interface using the current data and interaction state.
    return (
      <div className="pageMessage" role="status" aria-live="polite" aria-busy="true">
        Preparing page...
      </div>
    )
  }

  if (!hasAccess) {
    // Renders the interface using the current data and interaction state.
    return <Navigate to={kickoutPath} replace />
  }

  // Renders the interface using the current data and interaction state.
  return <ErrorBoundary>{children ?? <Outlet />}</ErrorBoundary>
}

export default ProtectedRoute
