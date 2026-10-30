# Kavach night-garden design system

Kavach uses this calm visual direction across its existing safety tools. The current Kavach Home screen is implemented in the shipped HTML/CSS app as a lightweight, accessible 2D flower scene; existing Shield, Vault, complaint and SOS workflows remain connected. The flower scene is intentionally GPU-free so it works on low-end phones and in reduced-motion mode. It is the visual and interaction starter for a later React Three Fiber scene.

## Design system

### Color

| Token | Value | Use |
|---|---|---|
| `--bloom-bg` | `#050507` | Night garden canvas |
| `--bloom-text` | `#F4EDE4` | Main text |
| `--bloom-blush` | `#F2B8C6` | Active flower, primary action |
| `--bloom-peach` | `#F6C9A0` | Sparkles and warm status |
| `--bloom-lavender` | `#B9A6E8` | Privacy flower and secondary glow |
| `--bloom-line` | `rgba(255,255,255,.55)` | Orbital hairlines |
| `--bloom-muted` | `#BCB4AD` | Supporting copy |

State is described with calm copy and gentle brightness changes. Existing risk classifications remain available within the safety workflows, but the Home page does not turn them into alarming status colors.

### Type scale

- Display: Cormorant Garamond italic, 58–94 px responsive, line-height `.95`.
- Section title: Cormorant Garamond, 40–52 px.
- Body: DM Sans, 14–16 px, line-height 1.5.
- Labels: DM Sans, 9–11 px, uppercase, letter-spacing `.2em`.
- Supporting labels: DM Sans, 12–13 px.

### Spacing and controls

Use a 4 px base grid: 4, 8, 12, 16, 24, 32, 48, 64 px. Main content max width is about 1280 px. Desktop gutters are 64–130 px; mobile gutters are 22 px. Cards use 18 px radii, pill buttons use 999 px radii, and every interactive target is at least 44 px high. Keyboard focus is a 2 px peach outline with a 3 px offset.

### Motion

| Token | Value | Use |
|---|---|---|
| `--motion-sway` | 10 s ease-in-out | Underwater flower drift |
| `--motion-breathe` | 8 s ease-in-out | Glow inhale/exhale |
| `--motion-orbit` | 34 s ease-in-out | Very slow line drift |
| `--motion-pollen` | 12 s linear | Sparse upward motes |
| `--motion-ui` | 300–1200 ms ease-in-out | Future scene focus and UI changes |

The Reduce motion control and OS preference disable idle motion. The accessible 2D flower illustration remains the fallback for screen readers and unsupported graphics contexts.

## Screens and responsive layouts

### 1. Home

At 1440 px, use a two-column hero: reassurance, status and actions on the left; a large orbital flower cluster on the right. Keep the copy width around 560 px. The top bar contains Kavach, Garden, Vault, Safety Space, and a persistent Quick exit. The hero says “Still protected. Still you.” and “YOUR SECURITY, IN FULL BLOOM”; status says “All quiet tonight.”

At 390 px, stack the flower scene above the copy, keep the headline between 56 and 60 px, stack two full-width actions, and keep Reduce motion and High contrast available. The quick-exit control stays in the top bar. Hide nonessential navigation in the compact header while leaving the existing bottom tab bar available.

### 2. Garden dashboard

At 1440 px, use a 4-column protection field across the main area, with a 320 px right status panel. Accounts, Devices, Privacy, and Passwords each map to one flower, its state, and a single plain-language next step. Thin orbit curves connect them. Use “One thing needs a little care” when action is needed.

At 390 px, show a single-column list or 2-column flower grid with a compact status card above it. Every flower card has a text label and status so meaning never depends on color or 3D alone. The existing Shield view is the current connected dashboard entry point.

### 3. Flower detail

At 1440 px, show one enlarged flower at left and an action panel at right, with a breadcrumb back to Garden. Give the user 2–3 clear one-tap actions such as “Turn on two-step sign-in”, “Review signed-in devices”, and “See recent changes”.

At 390 px, place the flower in a short header region above a single-column action list. Use 48 px minimum buttons and a persistent back control. Keep confirmation copy specific and nonjudgmental.

### 4. Safety Space

At 1440 px, render a quiet, neutral panel with three cards: check for unfamiliar monitoring apps, review location sharing, and report harassment. Keep emergency support available but visually separate from routine privacy checks. Quick exit remains top-level and does not require a confirmation.

At 390 px, cards become a single column with concise labels, one primary action each, and no sensitive details in notification text or page title. The current SOS and complaint screens are the connected feature implementations.

### 5. Onboarding

At 1440 px, use a centered 640 px content column and one large planting illustration. Three steps: choose what to protect, check one setting together, and plant the first flower. Show a three-dot progress indicator with text labels and allow skipping.

At 390 px, keep the same sequence in a single column; use a 44 px progress rail, a 48 px primary button, and one short paragraph per step. Offer reduced motion before the planting animation starts.

## Component inventory

- `KavachAppShell`: top navigation, neutral page title, Quick exit, mobile tab bar.
- `KavachHome`: reassurance copy, calm status, preference toggles, actions.
- `KavachFlowerCluster`: current CSS 2D scene; future R3F canvas can replace the visual while retaining its fallback.
- `FlowerNode`: protection type, open/dim state, accessible text label.
- `OrbitLines`, `Pollen`, `BloomSparkle`: decorative, aria-hidden scene accents.
- `CalmStatusCard`: plain-language status and next step.
- `ProtectionGrid`, `ProtectionDetail`: dashboard and one area’s settings.
- `SafetySpacePanel`, `SafetyActionCard`: stalkerware, location audit, harassment report.
- `OnboardingStepper`, `PlantingScene`: three-step setup.
- `PreferenceControls`: reduced motion and high contrast, stored locally.
- `QuickExitButton`: always-visible return to Kavach Home.

## Home scene starter

The current production starter is `.bloom-scene` in `frontend/index.html` with motion and responsive styling in `frontend/styles.css`. It composes five translucent CSS petals per flower, two orbital ellipses, sparse pollen, and a breathing glow. Motion runs slowly and `prefers-reduced-motion` plus the in-app toggle both stop it. The illustration is exposed to assistive technology as one concise description; decorative nodes are hidden from the accessibility tree.

For the requested React Three Fiber direction, preserve the same `KavachFlowerCluster` contract and provide an HTML/2D fallback before lazy-loading a compressed GLB. Keep bloom/DOF subtle, cap DPR to 1.5, reduce particles and postprocessing on mobile, and ease pointer parallax. Do not make WebGL a prerequisite for opening the app or using Safety Space.
