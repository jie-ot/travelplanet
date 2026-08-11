"use client"

import {
  useRef,
  type CSSProperties,
  type PointerEvent,
  type ReactNode,
} from "react"
import type { ProfileSpectrum, VisualTheme } from "@/types"

export interface JournalThemeTokens {
  primary: string
  secondary: string
  glow: string
  background: string
  paper: string
  paperAlt: string
  ink: string
  muted: string
  line: string
  stamp: string
}

export const THEME_COLORS: Record<
  VisualTheme,
  JournalThemeTokens
> = {
  forest_light: {
    primary: "#477A60",
    secondary: "#9DBB83",
    glow: "#C6D8AF",
    background: "#E9E7D6",
    paper: "#FBF7EA",
    paperAlt: "#E2E7D2",
    ink: "#29443B",
    muted: "#66736A",
    line: "#A8B7A0",
    stamp: "#A65E43",
  },
  ocean_blue: {
    primary: "#2F7690",
    secondary: "#78B8BD",
    glow: "#B8DCD8",
    background: "#E4ECE9",
    paper: "#FBF8ED",
    paperAlt: "#D9E9EA",
    ink: "#244F60",
    muted: "#637B81",
    line: "#9FBCC0",
    stamp: "#C56F4D",
  },
  sunset_orange: {
    primary: "#B85F42",
    secondary: "#DB9D66",
    glow: "#EDC49C",
    background: "#F0E2CF",
    paper: "#FFF8E9",
    paperAlt: "#F2D6BC",
    ink: "#6D3E32",
    muted: "#80675C",
    line: "#D4AB8F",
    stamp: "#A8442C",
  },
  museum_gold: {
    primary: "#8B6A35",
    secondary: "#C59C5A",
    glow: "#E2C88A",
    background: "#E9DFC8",
    paper: "#FBF4DF",
    paperAlt: "#E2D1AE",
    ink: "#4B3B29",
    muted: "#786B57",
    line: "#C8B48E",
    stamp: "#A54832",
  },
  city_neon: {
    primary: "#46688D",
    secondary: "#D16B78",
    glow: "#BAC7D8",
    background: "#E5E6E1",
    paper: "#F9F5E9",
    paperAlt: "#DDE2E8",
    ink: "#33495F",
    muted: "#687580",
    line: "#AAB6C1",
    stamp: "#D05742",
  },
  night_purple: {
    primary: "#655584",
    secondary: "#9C789E",
    glow: "#CDBAD3",
    background: "#E5DEE8",
    paper: "#F8F1F2",
    paperAlt: "#DCD0E3",
    ink: "#3F3651",
    muted: "#746A7A",
    line: "#B8AABF",
    stamp: "#A95D68",
  },
  snow_silver: {
    primary: "#587681",
    secondary: "#99ADB0",
    glow: "#D5E1DC",
    background: "#E8ECE9",
    paper: "#FBFAF1",
    paperAlt: "#DCE3E0",
    ink: "#344B54",
    muted: "#687980",
    line: "#AAB9BC",
    stamp: "#B26049",
  },
  desert_amber: {
    primary: "#A4683F",
    secondary: "#C99A5E",
    glow: "#DFBD83",
    background: "#EADDC5",
    paper: "#FBF3DF",
    paperAlt: "#E4C9A1",
    ink: "#5E442D",
    muted: "#7C6A55",
    line: "#C8AA80",
    stamp: "#A84E34",
  },
}

type PlanetStyle = CSSProperties & Record<`--${string}`, string | number>

interface PersonaPlanetProps {
  title: string
  subtitle: string
  theme: VisualTheme
  spectrums?: ProfileSpectrum[]
}

export function PersonaPlanet({ title, subtitle, theme, spectrums = [] }: PersonaPlanetProps) {
  const stageRef = useRef<HTMLDivElement>(null)
  const colors = THEME_COLORS[theme]
  const spectrumValue = (id: ProfileSpectrum["id"]) =>
    spectrums.find((item) => item.id === id)?.value ?? 50
  const environment = spectrumValue("environment")
  const depth = spectrumValue("depth")
  const planning = spectrumValue("planning")
  const social = spectrumValue("social")
  const style: PlanetStyle = {
    "--planet-primary": colors.primary,
    "--planet-secondary": colors.secondary,
    "--planet-glow": colors.glow,
    "--planet-bg": colors.background,
    "--journal-skew": `${((50 - planning) / 50) * 1.15}deg`,
    "--journal-density": `${0.38 + depth / 180 + social / 360}`,
    "--nature-weight": `${1 - environment / 100}`,
    "--city-weight": `${environment / 100}`,
    "--motif-shift": `${((environment - 50) / 50) * 4}px`,
    "--cluster-shift": `${((social - 50) / 50) * 3}px`,
    "--wash-focus": `${30 + environment * 0.4}%`,
    "--route-speed": `${Math.max(13, 24 - planning / 9)}s`,
  }

  function handlePointerMove(event: PointerEvent<HTMLDivElement>) {
    const stage = stageRef.current
    if (!stage) return
    const rect = stage.getBoundingClientRect()
    const x = ((event.clientX - rect.left) / rect.width - 0.5) * 2
    const y = ((event.clientY - rect.top) / rect.height - 0.5) * 2
    stage.style.setProperty("--parallax-x", `${x * 7}px`)
    stage.style.setProperty("--parallax-y", `${y * 5}px`)
  }

  function resetParallax() {
    const stage = stageRef.current
    if (!stage) return
    stage.style.setProperty("--parallax-x", "0px")
    stage.style.setProperty("--parallax-y", "0px")
  }

  return (
    <section
      ref={stageRef}
      className="persona-planet-stage"
      data-theme={theme}
      style={style}
      onPointerMove={handlePointerMove}
      onPointerLeave={resetParallax}
      aria-label={`${title}旅行人格手账`}
    >
      <div className="persona-aurora" aria-hidden />
      <div className="persona-stars" aria-hidden />
      <JournalDoodle theme={theme} />
      <div className="persona-orbit persona-orbit-one" aria-hidden />
      <div className="persona-orbit persona-orbit-two" aria-hidden />
      <div className="persona-sphere-wrap" aria-hidden>
        <div className="persona-sphere">
          <span className="persona-sphere-shine" />
          <span className="persona-sphere-shadow" />
        </div>
      </div>
      <div className="persona-title-lockup">
        <p>{subtitle}</p>
        <h1>{title}</h1>
      </div>
    </section>
  )
}

function JournalDoodle({ theme }: { theme: VisualTheme }) {
  let drawing: ReactNode

  switch (theme) {
    case "forest_light":
      drawing = (
        <>
          <path d="M8 78 42 42l19 22 28-42 36 56M20 77h110" />
          <path d="M318 20c-24 18-36 43-37 74m4-45c14-8 25-9 37-5m-42 19c-13-8-25-9-36-4m39 14c12-5 23-5 33 0" />
        </>
      )
      break
    case "ocean_blue":
      drawing = (
        <>
          <path d="M4 61c20-14 38 14 59 0s39 14 60 0 39 14 60 0M4 78c20-14 38 14 59 0s39 14 60 0" />
          <path d="M330 32c15 15 15 33 0 48-15-15-15-33 0-48Zm0 4v39m-15-27 15 27 15-27" />
        </>
      )
      break
    case "sunset_orange":
      drawing = (
        <>
          <ellipse cx="60" cy="63" rx="43" ry="11" />
          <path d="M20 62c4 38 75 38 81 0M40 45c-8-13 9-17 1-31m22 32c-8-13 9-17 1-31m19 33c-8-13 9-17 1-31" />
          <path d="m292 30 93 54m-86-65 93 54" />
        </>
      )
      break
    case "museum_gold":
      drawing = (
        <>
          <path d="m23 49 54-28 54 28H23Zm10 0v38m25-38v38m38-38v38m25-38v38H21" />
          <path d="M291 84h104M302 84V45h82v39M296 45l47-26 47 26M326 53v22m34-22v22" />
        </>
      )
      break
    case "city_neon":
      drawing = (
        <>
          <path d="M7 86V35h37v51m0-65h36v65m0-42h43v42m-92-34h14m12-16h12m28 21h10" />
          <path d="M284 78h105M298 78l18-52 22 52 20-34 25 34M291 64h88" />
        </>
      )
      break
    case "night_purple":
      drawing = (
        <>
          <path d="M44 21a36 36 0 1 0 34 54A31 31 0 0 1 44 21Z" />
          <path d="M287 84V34h85v50m-73 0V48h27v36m12 0V58h23v26M280 84h103" />
          <path d="m111 31 2 6 6 2-6 2-2 6-2-6-6-2 6-2Z" />
        </>
      )
      break
    case "snow_silver":
      drawing = (
        <>
          <path d="M6 82 43 47l19 18 31-40 43 57M19 82h125" />
          <path d="M287 27h103v58H287zM298 27v13m13-13v8m13-8v13m13-13v8m13-8v13m13-13v8m13-8v13" />
        </>
      )
      break
    case "desert_amber":
      drawing = (
        <>
          <path d="M4 78c25-13 48-15 69-5 23 11 45 9 68-5M4 86h145" />
          <path d="M291 85c19-15 31-31 43-48 15 17 28 33 53 48M339 83l-5-46m5 46 9-19m-9 19-10-20" />
          <circle cx="101" cy="27" r="16" />
        </>
      )
      break
  }

  return (
    <svg
      className="persona-journal-doodle"
      viewBox="0 0 400 100"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.35"
      aria-hidden
    >
      {drawing}
    </svg>
  )
}
