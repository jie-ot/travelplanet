"use client"

import { useEffect, useRef, useState } from "react"
import Image from "next/image"
import {
  Check,
  ChevronRight,
  FileChartColumn,
  Flag,
  ImagePlus,
  Map,
  MapPin,
  Plane,
  Send,
  X,
} from "lucide-react"
import { useApp, type GenerationProgress } from "@/components/shared/app-context"
import { UserBadge } from "@/components/shared/user-badge"
import { PlanetLoader } from "@/components/shared/planet-loader"
import { PLACEHOLDER_IMAGE, handleImageError, resolveAssetUrl, shortId } from "@/lib/asset"
import { cn } from "@/lib/utils"
import styles from "./home-view.module.css"

interface LocalPhoto {
  id: string
  url: string
  file: File
}

const MAX_PHOTOS = 50
const HERO_IMAGE = "/images/home-hero-lake.png"
const HERO_TITLE = "/images/home-title-brush.png"
const POSTCARD_MAIN = "/images/home-hero-lake.png"
const POSTCARD_BALLOON = "/images/home-postcard-balloon.png"
const POSTCARD_LAKE = "/images/home-postcard-alpine-lake.png"

export function HomeView() {
  const {
    navigate,
    toast,
    toastCode,
    generateArtifacts,
    generating,
    generationProgress,
    postcardGroups,
    beginNewPlan,
  } = useApp()
  const [photos, setPhotos] = useState<LocalPhoto[]>([])
  const [requirements, setRequirements] = useState("")
  const [genPostcards, setGenPostcards] = useState(true)
  const [genReport, setGenReport] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const latest = postcardGroups[0]

  useEffect(() => {
    return () => {
      photos.forEach((photo) => URL.revokeObjectURL(photo.url))
    }
    // 仅在卸载时清理当前批次的本地预览 URL。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const onPickFiles = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? [])
    const remaining = MAX_PHOTOS - photos.length
    if (remaining <= 0) {
      toast(`最多上传 ${MAX_PHOTOS} 张照片`, "info")
      event.target.value = ""
      return
    }
    const picked = files.slice(0, remaining)
    if (files.length > remaining) {
      toast(`最多上传 ${MAX_PHOTOS} 张，已添加前 ${picked.length} 张`, "info")
    }
    const next = picked.map((file) => ({ id: shortId("ph"), url: URL.createObjectURL(file), file }))
    setPhotos((current) => [...current, ...next])
    event.target.value = ""
  }

  const removePhoto = (id: string) =>
    setPhotos((current) => {
      const target = current.find((photo) => photo.id === id)
      if (target) URL.revokeObjectURL(target.url)
      return current.filter((photo) => photo.id !== id)
    })

  const handleSend = async () => {
    if (!genPostcards && !genReport) {
      toastCode(1002)
      return
    }
    if (photos.length === 0) {
      toast("请先上传照片再生成", "info")
      return
    }

    const result = await generateArtifacts({
      files: photos.map((photo) => photo.file),
      requirements: requirements.trim() || "请根据照片生成旅行明信片",
      options: { generatePostcards: genPostcards, generateReport: genReport },
    })

    if (!result) return
    photos.forEach((photo) => URL.revokeObjectURL(photo.url))
    setPhotos([])
    setRequirements("")
    toast("生成完成", "success")
    navigate({ page: result.postcardGroup ? "postcards" : "reports" })
  }

  return (
    <div className={styles.home}>
      <section className={styles.hero} aria-labelledby="home-title">
        <div className={styles.starField} aria-hidden />
        <div className={styles.contours} aria-hidden />
        <span className={styles.heroSparkOne} aria-hidden>✦</span>
        <span className={styles.heroSparkTwo} aria-hidden>✦</span>
        <span className={styles.heroDot} aria-hidden />

        <div className={styles.profile}>
          <UserBadge />
        </div>

        <div className={styles.planet} aria-hidden>
          <span className={styles.planetRing} />
          <span className={styles.orbitDot} />
          <div className={styles.planetImage}>
            <Image
              src={HERO_IMAGE}
              alt=""
              fill
              priority
              sizes="(max-width: 448px) 58vw, 250px"
              className={styles.coverImage}
              onError={handleImageError}
            />
          </div>
        </div>

        <Compass className={styles.heroCompass} />
        <svg className={styles.airmailLines} viewBox="0 0 150 36" aria-hidden>
          <path d="M0 18c30-25 56 21 88-4s43 5 62-8" />
          <path d="M0 25c30-25 56 21 88-4s43 5 62-8" />
          <path d="M0 32c30-25 56 21 88-4s43 5 62-8" />
        </svg>
        <Compass className={styles.heroBottomCompass} />
        <svg className={styles.heroTrail} viewBox="0 0 250 54" preserveAspectRatio="none" aria-hidden>
          <path d="M0 28c54 24 110 20 151 9 38-10 60-30 99-26" />
          <circle cx="151" cy="37" r="3" />
          <path d="m196 23 9-8-3 12-3-4-3 0Z" />
        </svg>

        <div className={styles.heroCopy}>
          <h1 id="home-title">
            <span>旅行星球</span>
            <Image src={HERO_TITLE} alt="" fill priority sizes="205px" />
          </h1>
          <p>懂你的旅行助手 ·</p>
          <p>把旅程变成专属明信片与人格报告</p>
        </div>

        <svg className={styles.heroTear} viewBox="0 0 430 38" preserveAspectRatio="none" aria-hidden>
          <path className={styles.tearShadow} d="M0 23C15 22 27 16 43 20C59 24 71 15 87 19C104 23 116 14 132 18C149 22 160 13 176 17C193 21 205 14 220 18C237 22 249 13 265 17C282 21 294 12 310 16C327 20 339 12 355 16C372 20 385 12 401 15C413 18 421 13 430 11L430 38H0Z" />
          <path className={styles.tearFiber} d="M0 21C15 20 27 14 43 18C59 22 71 13 87 17C104 21 116 12 132 16C149 20 160 11 176 15C193 19 205 12 220 16C237 20 249 11 265 15C282 19 294 10 310 14C327 18 339 10 355 14C372 18 385 10 401 13C413 16 421 11 430 9L430 38H0Z" />
          <path className={styles.tearPaper} d="M0 19C15 18 27 12 43 16C59 20 71 11 87 15C104 19 116 10 132 14C149 18 160 9 176 13C193 17 205 10 220 14C237 18 249 9 265 13C282 17 294 8 310 12C327 16 339 8 355 12C372 16 385 8 401 11C413 14 421 9 430 7L430 38H0Z" />
        </svg>
      </section>

      <section className={styles.collage} aria-label="旅行明信片预览">
        <div className={styles.porthole} aria-hidden>
          <span><i /></span>
        </div>

        <Postcard className={styles.postcardLake} src={POSTCARD_LAKE} alt="山谷湖泊旅行照片" />
        <Postcard className={styles.postcardBalloon} src={POSTCARD_BALLOON} alt="热气球古镇旅行照片" />
        <Postcard className={styles.postcardMain} src={POSTCARD_MAIN} alt="湖畔古镇日出旅行照片" seal />

        <svg className={styles.collageRoute} viewBox="0 0 430 150" preserveAspectRatio="none" aria-hidden>
          <path className={styles.routeBlue} d="M282 18c89 8 129 35 99 65-20 20-83 46-151 50" />
          <path className={styles.routeOrange} d="M230 133c72 3 134-18 151-50" />
          <circle cx="293" cy="132" r="4" />
          <circle cx="340" cy="122" r="4" />
          <path className={styles.routePlane} d="m379 82 17-9-7 17-3-7-7-1Z" />
        </svg>
        <StampMark className={styles.collageStamp} />
      </section>

      <section className={styles.postcardEntry}>
        <svg className={styles.collageTear} viewBox="0 0 430 150" preserveAspectRatio="none" aria-hidden>
          <path className={styles.collageTearShadow} d="M0 20C38 22 68 31 104 39C147 49 189 57 232 69C275 80 316 89 358 101C387 109 410 120 430 127L430 150H0Z" />
          <path className={styles.collageTearFiber} d="M0 16C38 19 68 27 104 36C147 46 189 54 232 66C275 77 316 86 358 98C387 106 410 117 430 124L430 150H0Z" />
          <path className={styles.collageTearPaper} d="M0 12C38 15 68 24 104 32C147 42 189 50 232 62C275 73 316 82 358 94C387 102 410 113 430 120L430 150H0Z" />
        </svg>
        <button type="button" onClick={() => navigate({ page: "postcards" })} aria-label="查看明信片">
          <span className={styles.entryCopy}>
            <strong>查看明信片<em>✦</em></strong>
            <small>把照片变成旅行明信片</small>
          </span>
          <span className={styles.stitchLine} aria-hidden />
          <span className={styles.arrowStamp} aria-hidden>
            <svg viewBox="0 0 48 48">
              <path d="M9 24h27M27 14l10 10-10 10" />
            </svg>
          </span>
        </button>
      </section>

      <nav className={styles.shortcuts} aria-label="首页功能入口">
        <svg className={styles.shortcutRoute} viewBox="0 0 430 92" preserveAspectRatio="none" aria-hidden>
          <path d="M38 69c65-8 72-48 139-45 53 2 37 48 94 48 56 0 60-34 125-25" />
        </svg>
        <Flag className={styles.routeFlag} aria-hidden />
        <MapPin className={styles.routePin} aria-hidden />

        <ShortcutButton
          className={styles.reportShortcut}
          icon={<FileChartColumn aria-hidden />}
          title="查看报告"
          description="你的旅行人格"
          onClick={() => navigate({ page: "reports" })}
        />
        <ShortcutButton
          className={styles.planningShortcut}
          icon={<Map aria-hidden />}
          title="旅行规划"
          description="规划下一段旅程"
          onClick={() => {
            beginNewPlan()
            navigate({ page: "planning" })
          }}
          coral
        />
      </nav>

      <section className={styles.latestSlot} aria-label="最近旅程">
        {photos.length > 0 ? (
          <div className={styles.photoTray} aria-label={`已选 ${photos.length} 张照片`}>
            <span>已选 {photos.length}/{MAX_PHOTOS}</span>
            <div>
              {photos.map((photo) => (
                <figure key={photo.id}>
                  <img src={photo.url || PLACEHOLDER_IMAGE} alt="待生成照片" onError={handleImageError} />
                  <button type="button" onClick={() => removePhoto(photo.id)} aria-label="移除照片">
                    <X aria-hidden />
                  </button>
                </figure>
              ))}
            </div>
          </div>
        ) : latest ? (
          <button
            type="button"
            className={styles.latestTicket}
            onClick={() => navigate({ page: "postcard-collection", groupId: latest.id })}
          >
            <img
              src={resolveAssetUrl(latest.coverImage) || PLACEHOLDER_IMAGE}
              alt={`${latest.location} 明信片封面`}
              onError={handleImageError}
            />
            <span className={styles.latestText}>
              <small><Plane aria-hidden />继续你的旅程</small>
              <strong>{latest.location}</strong>
              <span>{latest.dateLabel} · 共 {latest.postcards.length} 张明信片</span>
            </span>
            <StampMark className={styles.ticketStamp} />
            <ChevronRight className={styles.latestChevron} aria-hidden />
          </button>
        ) : null}
      </section>

      <section className={styles.composer} aria-label="生成旅行内容">
        <div className={styles.envelope}>
          <svg className={styles.postmarkWaves} viewBox="0 0 84 34" aria-hidden>
            <path d="M0 8c15-12 29 12 44 0s29 12 40 0" />
            <path d="M0 17c15-12 29 12 44 0s29 12 40 0" />
            <path d="M0 26c15-12 29 12 44 0s29 12 40 0" />
          </svg>
          <div className={styles.toggleRow}>
            <ToggleChip active={genPostcards} onClick={() => setGenPostcards((value) => !value)} label="生成明信片" />
            <ToggleChip active={genReport} onClick={() => setGenReport((value) => !value)} label="生成报告" />
          </div>
          <div className={styles.inputRow}>
            <button type="button" className={styles.uploadButton} onClick={() => fileRef.current?.click()} aria-label="上传照片">
              <ImagePlus aria-hidden />
            </button>
            <input ref={fileRef} type="file" accept="image/*" multiple hidden onChange={onPickFiles} />
            <textarea
              value={requirements}
              onChange={(event) => setRequirements(event.target.value)}
              rows={1}
              placeholder="请输入您的需求..."
              aria-label="生成要求"
            />
            <button type="button" className={styles.sendButton} onClick={handleSend} disabled={generating} aria-label="发送">
              <Send aria-hidden />
            </button>
          </div>
        </div>
      </section>

      {generating ? (
        <GenerationWaiting progress={generationProgress} />
      ) : null}
    </div>
  )
}

function GenerationWaiting({ progress }: { progress: GenerationProgress | null }) {
  const [tipIndex, setTipIndex] = useState(0)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)

  useEffect(() => {
    const elapsedTimer = window.setInterval(() => setElapsedSeconds((seconds) => seconds + 1), 1000)
    const tipTimer =
      progress?.phase === "creating"
        ? window.setInterval(() => setTipIndex((index) => index + 1), 4600)
        : null

    return () => {
      window.clearInterval(elapsedTimer)
      if (tipTimer) window.clearInterval(tipTimer)
    }
  }, [progress?.phase])

  const phase = progress?.phase ?? "preparing"
  const completed = progress?.completed ?? 0
  const total = progress?.total ?? 0
  const options = progress?.options ?? { generatePostcards: true, generateReport: false }
  const isCreating = phase === "creating"
  const uploadPercent = total > 0 ? Math.round((completed / total) * 100) : 0
  const tips = generationTips(options)
  const outputName =
    options.generatePostcards && options.generateReport
      ? "明信片与人格报告"
      : options.generateReport
        ? "旅行人格报告"
        : "旅行明信片"
  const minutes = Math.floor(elapsedSeconds / 60)
  const seconds = String(elapsedSeconds % 60).padStart(2, "0")
  const statusText = isCreating
    ? tips[tipIndex % tips.length]
    : phase === "preparing"
      ? `正在读取 ${total} 张照片的时间与位置信息`
      : `正在上传照片 · ${completed}/${total}`

  return (
    <div className={styles.generating} aria-busy="true" aria-label="内容生成进度">
      <div className={styles.progressCard} data-generation-progress-card>
        <div className={styles.progressVisual}>
          <PlanetLoader label={null} className="!py-0" />
          <span>{isCreating ? "AI" : `${uploadPercent}%`}</span>
        </div>

        <div className={styles.progressCopy} role="status" aria-live="polite" aria-atomic="true">
          <span className={styles.progressEyebrow}>第 {isCreating ? 2 : 1} / 3 步</span>
          <h2>{isCreating ? `正在生成${outputName}` : "正在让照片准备就绪"}</h2>
          <p>{statusText}</p>
        </div>

        {isCreating ? (
          <div className={cn(styles.uploadProgress, styles.creatingProgress)} aria-hidden>
            <span />
          </div>
        ) : (
          <div
            className={styles.uploadProgress}
            role="progressbar"
            aria-label="照片上传进度"
            aria-valuemin={0}
            aria-valuemax={total}
            aria-valuenow={completed}
          >
            <span style={{ width: `${uploadPercent}%` }} />
          </div>
        )}

        <ol className={styles.progressSteps} aria-label="生成步骤">
          <li className={isCreating ? styles.stepDone : styles.stepActive}>
            <span>{isCreating ? <Check aria-hidden /> : "1"}</span>
            <small>照片就绪</small>
          </li>
          <li className={isCreating ? styles.stepActive : styles.stepPending}>
            <span>2</span>
            <small>AI 创作</small>
          </li>
          <li className={styles.stepPending}>
            <span>3</span>
            <small>保存结果</small>
          </li>
        </ol>

        <div className={styles.progressMeta}>
          <span>已等待 {minutes}:{seconds}</span>
          <span>可能需要3-5分钟</span>
        </div>
      </div>
    </div>
  )
}

function generationTips(options: GenerationProgress["options"]): string[] {
  const tips = ["正在辨认照片中的地点、时间与旅行氛围"]
  if (options.generatePostcards) {
    tips.push("正在挑选适合明信片表达的画面与故事")
  }
  if (options.generateReport) {
    tips.push("正在归纳旅行偏好、人格线索与五维数据")
  }
  tips.push("生成完成后会自动保存，无需重复点击")
  return tips
}

function Postcard({ className, src, alt, seal = false }: { className: string; src: string; alt: string; seal?: boolean }) {
  return (
    <div className={cn(styles.postcard, className)}>
      <div>
        <Image
          src={src}
          alt={alt}
          fill
          sizes="220px"
          loading={seal ? "eager" : "lazy"}
          className={styles.coverImage}
          onError={handleImageError}
        />
      </div>
      {seal ? <span className={styles.photoSeal}>TRAVEL PLANET<br />MOUNTAIN · LAKE</span> : null}
    </div>
  )
}

function ShortcutButton({
  className,
  icon,
  title,
  description,
  onClick,
  coral = false,
}: {
  className: string
  icon: React.ReactNode
  title: string
  description: string
  onClick: () => void
  coral?: boolean
}) {
  return (
    <button type="button" className={cn(styles.shortcut, className, coral && styles.coral)} onClick={onClick}>
      <span className={styles.shortcutIcon}>{icon}</span>
      <span className={styles.shortcutLabel}>
        <strong>{title}</strong>
        <small>{description}</small>
      </span>
      <ChevronRight aria-hidden />
    </button>
  )
}

function ToggleChip({ active, onClick, label }: { active: boolean; onClick: () => void; label: string }) {
  return (
    <button type="button" onClick={onClick} aria-pressed={active} className={cn(styles.toggle, active && styles.toggleActive)}>
      <span>{active ? <Check aria-hidden /> : null}</span>
      {label}
    </button>
  )
}

function Compass({ className }: { className: string }) {
  return (
    <svg className={className} viewBox="0 0 72 72" aria-hidden>
      <circle cx="36" cy="36" r="22" />
      <circle cx="36" cy="36" r="14" />
      <path d="m36 4 5 27 27 5-27 5-5 27-5-27-27-5 27-5Z" />
      <path d="m36 17 3 16 16 3-16 3-3 16-3-16-16-3 16-3Z" />
    </svg>
  )
}

function StampMark({ className }: { className: string }) {
  return (
    <svg className={className} viewBox="0 0 120 58" aria-hidden>
      <circle cx="30" cy="29" r="21" />
      <circle cx="30" cy="29" r="16" />
      <path d="M53 17c21-9 40 10 64-1M52 25c22-8 41 10 66-1M52 34c23-8 42 10 65-1M52 43c20-7 39 8 63 0" />
      <path d="m30 15 3 11 11 3-11 3-3 11-3-11-11-3 11-3Z" />
    </svg>
  )
}
