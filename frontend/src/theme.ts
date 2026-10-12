/**
 * The HUD theme.
 *
 * One dark "mission-control" design language for every surface: near-black
 * translucent panels, hairline dividers, uppercase monospace micro-labels, and
 * the four signal colours the 3D tactical renderer already uses (TECH_STATES in
 * techMaterial.ts) so the 2D chrome and the 3D scene speak the same palette.
 *
 * System font stacks, deliberately: a demo must survive a machine with no
 * network, and webfont loading on a full-bleed canvas is a flash of nothing.
 */
import { createTheme } from '@mui/material/styles'

export const MONO =
  "'Cascadia Code', 'JetBrains Mono', 'SF Mono', ui-monospace, Consolas, 'Courier New', monospace"

export const SANS =
  "'Inter', 'SF Pro Display', system-ui, 'Segoe UI', 'Helvetica Neue', sans-serif"

/** The four signal colours, shared with the tactical 3D renderer. */
export const SIGNAL = {
  green: '#5cff9d',
  cyan: '#3fe0ff',
  amber: '#ff9d2e',
  red: '#ff5c5c',
} as const

const HAIRLINE = 'rgba(160, 190, 175, 0.16)'
const PANEL = 'rgba(10, 16, 14, 0.86)'

const theme = createTheme({
  palette: {
    mode: 'dark',
    primary: { main: SIGNAL.green, contrastText: '#04120a' },
    secondary: { main: SIGNAL.cyan },
    success: { main: SIGNAL.green },
    warning: { main: SIGNAL.amber },
    error: { main: SIGNAL.red },
    info: { main: SIGNAL.cyan },
    background: { default: '#05080a', paper: PANEL },
    text: { primary: '#dce8e2', secondary: '#7d928a' },
    divider: HAIRLINE,
    action: {
      hover: 'rgba(92, 255, 157, 0.08)',
      selected: 'rgba(92, 255, 157, 0.14)',
      focus: 'rgba(92, 255, 157, 0.12)',
    },
  },
  shape: { borderRadius: 3 },
  typography: {
    fontFamily: SANS,
    h6: { fontSize: 15, fontWeight: 600, letterSpacing: '-0.01em' },
    subtitle2: { fontSize: 12.5, fontWeight: 600 },
    body2: { fontSize: 12.5, lineHeight: 1.55 },
    overline: {
      fontFamily: MONO,
      fontSize: 9.5,
      fontWeight: 600,
      letterSpacing: '0.22em',
      textTransform: 'uppercase',
      color: '#7d928a',
    },
    caption: {
      fontSize: 11,
      lineHeight: 1.5,
      fontFamily: MONO,
      fontFeatureSettings: "'tnum'",
    },
    button: {
      fontFamily: MONO,
      fontSize: 11.5,
      fontWeight: 600,
      letterSpacing: '0.1em',
      textTransform: 'uppercase' as const,
    },
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: { backgroundColor: '#05080a' },
      },
    },
    MuiPaper: {
      styleOverrides: {
        outlined: {
          borderColor: HAIRLINE,
          backgroundImage: 'none',
        },
        root: {
          backgroundImage: 'none',
        },
      },
    },
    MuiDrawer: {
      styleOverrides: {
        paper: {
          backgroundColor: PANEL,
          backdropFilter: 'blur(16px)',
          borderRight: `1px solid ${HAIRLINE}`,
        },
      },
    },
    MuiButton: {
      styleOverrides: {
        root: { borderRadius: 2 },
        contained: {
          backgroundColor: SIGNAL.green,
          color: '#04120a',
          boxShadow: 'none',
          '&:hover': {
            backgroundColor: '#7dffB4',
            boxShadow: `0 0 18px rgba(92, 255, 157, 0.25)`,
          },
          '&:disabled': { backgroundColor: 'rgba(92,255,157,0.12)', color: 'rgba(220,232,226,0.3)' },
        },
        outlined: {
          borderColor: HAIRLINE,
          color: '#dce8e2',
          '&:hover': { borderColor: 'rgba(92,255,157,0.5)', backgroundColor: 'rgba(92,255,157,0.06)' },
        },
        sizeLarge: { padding: '10px 20px' },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: {
          fontFamily: MONO,
          fontSize: 10,
          letterSpacing: '0.08em',
          textTransform: 'uppercase' as const,
          height: 20,
          borderRadius: 2,
        },
        outlined: { borderColor: HAIRLINE },
        filled: { backgroundColor: 'rgba(92,255,157,0.12)', color: SIGNAL.green },
      },
    },
    MuiSlider: {
      styleOverrides: {
        root: { height: 2, padding: '10px 0' },
        rail: { backgroundColor: 'rgba(160,190,175,0.22)', height: 2 },
        track: { backgroundColor: SIGNAL.green, height: 2 },
        thumb: {
          width: 10,
          height: 10,
          borderRadius: 1,
          backgroundColor: '#05080a',
          border: `1.5px solid ${SIGNAL.green}`,
          boxShadow: `0 0 8px rgba(92,255,157,0.45)`,
          '&:hover, &.Mui-focusVisible': { boxShadow: `0 0 12px rgba(92,255,157,0.7)` },
        },
        valueLabel: { fontFamily: MONO },
        mark: { backgroundColor: 'rgba(160,190,175,0.3)', width: 1 },
      },
    },
    MuiSwitch: {
      styleOverrides: {
        root: { padding: 4 },
        switchBase: { color: 'rgba(160,190,175,0.4)' },
        checked: { color: SIGNAL.green, '& + .MuiSwitch-track': { backgroundColor: 'rgba(92,255,157,0.3)' } },
        track: { backgroundColor: 'rgba(160,190,175,0.18)', borderRadius: 8 },
        thumb: { backgroundColor: '#dce8e2', width: 11, height: 11 },
      },
    },
    MuiOutlinedInput: {
      styleOverrides: {
        root: {
          backgroundColor: 'rgba(5, 8, 10, 0.55)',
          fontSize: 12.5,
          '& fieldset': { borderColor: HAIRLINE },
          '&:hover fieldset': { borderColor: 'rgba(160,190,175,0.35)' },
          '&.Mui-focused fieldset': { borderColor: 'rgba(92,255,157,0.6)' },
        },
        input: { fontSize: 12.5 },
        multiline: { fontFamily: SANS },
      },
    },
    MuiToggleButton: {
      styleOverrides: {
        root: {
          fontFamily: MONO,
          fontSize: 10.5,
          letterSpacing: '0.12em',
          textTransform: 'uppercase' as const,
          color: '#7d928a',
          borderColor: HAIRLINE,
          '&.Mui-selected': {
            color: SIGNAL.green,
            backgroundColor: 'rgba(92,255,157,0.1)',
            borderColor: 'rgba(92,255,157,0.4)',
          },
        },
      },
    },
    MuiAlert: {
      styleOverrides: {
        root: { fontSize: 11.5, borderRadius: 2 },
        outlinedInfo: { borderColor: 'rgba(63,224,255,0.3)', color: '#bfe9f2' },
        outlinedError: { borderColor: 'rgba(255,92,92,0.4)' },
        outlinedWarning: { borderColor: 'rgba(255,157,46,0.4)' },
      },
    },
    MuiFab: {
      styleOverrides: {
        root: {
          backgroundColor: 'rgba(10,16,14,0.86)',
          backdropFilter: 'blur(12px)',
          border: `1px solid ${HAIRLINE}`,
          color: '#dce8e2',
          boxShadow: '0 4px 24px rgba(0,0,0,0.5)',
          '&:hover': { backgroundColor: 'rgba(20,28,25,0.9)' },
        },
        sizeSmall: { width: 32, height: 32 },
      },
    },
    MuiTooltip: { styleOverrides: { tooltip: { fontFamily: MONO, fontSize: 11 } } },
  },
})

export default theme
