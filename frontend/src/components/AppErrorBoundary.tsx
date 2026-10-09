/**
 * Last-resort boundary so a render failure is a sentence, not a white page.
 *
 * ## Why this exists
 *
 * Measured twice in one session, both times on a malformed API payload: something
 * read `.length` of undefined inside a render, React unmounted the tree, and the app
 * presented as *completely blank* — no message, no controls, nothing to click. The
 * individual causes were fixed, but they were two instances of one class, and the
 * class is not closed: any future `x.y.length` on an unvalidated payload does exactly
 * the same thing.
 *
 * The specific bugs are worth fixing at their source, because a boundary that hides a
 * broken panel is worse than a blank page — it turns a loud failure into a quiet one.
 * This exists for the ones that have not been found yet, and it deliberately shows the
 * message rather than swallowing it.
 */
import { Component, type ErrorInfo, type ReactNode } from 'react'
import Alert from '@mui/material/Alert'
import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

export default class AppErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Kept in the console deliberately: this is a bug, not a user error, and the
    // component stack is what makes it findable.
    console.error('[sandbox] render failed:', error, info.componentStack)
  }

  render(): ReactNode {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <Box sx={{ p: 3 }}>
        <Alert severity="error" variant="outlined">
          <Typography variant="subtitle2" gutterBottom>
            The map failed to render
          </Typography>
          <Typography variant="body2">
            Something in the scene data was not in the shape this app expects. The
            detail is in the browser console.
          </Typography>
          <Typography variant="caption" component="div" sx={{ mt: 1, opacity: 0.8 }}>
            {error.message}
          </Typography>
        </Alert>
      </Box>
    )
  }
}
