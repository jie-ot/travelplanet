"use client"

import { useEffect, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react"
import {
  CalendarDays,
  ChevronDown,
  Download,
  Footprints,
  Luggage,
  Map as MapIcon,
  MapPin,
  Navigation,
  Route,
  Ticket,
  TriangleAlert,
  Utensils,
  X,
} from "lucide-react"
import { ConfirmDialog } from "@/components/shared/confirm-dialog"
import { useApp } from "@/components/shared/app-context"
import { resolveAssetUrl } from "@/lib/asset"
import { saveTravelImage } from "@/lib/postcard-save"
import type { DailyMap, ItineraryData, Schedule } from "@/types"

const PERIOD_ICONS: Record<string, string> = {
  上午: "☀",
  中午: "◉",
  下午: "◐",
  晚上: "✦",
}

function destinationMood(destination: string) {
  if (/苏州|江南|园林|杭州/.test(destination)) return "suzhou"
  if (/海|岛|三亚|厦门|青岛/.test(destination)) return "ocean"
  if (/山|林|峡谷|草原|大理|丽江/.test(destination)) return "forest"
  if (/夜|上海|重庆|香港|深圳/.test(destination)) return "neon"
  return "city"
}

export function ItineraryDetail({ data }: { data: ItineraryData }) {
  const { registerBackHandler, toast } = useApp()
  const { trip_info, preparations, bookings, food_recommendations, itinerary, advisories } = data
  const [activeDay, setActiveDay] = useState(itinerary[0]?.id ?? "")
  const [expandedSchedules, setExpandedSchedules] = useState<Set<string>>(() => new Set())
  const [bookingOpen, setBookingOpen] = useState(false)
  const [foodOpen, setFoodOpen] = useState(false)
  const [openMapDays, setOpenMapDays] = useState<Set<string>>(
    () => new Set(itinerary[0]?.daily_maps?.length ? [itinerary[0].id] : []),
  )
  const [previewMap, setPreviewMap] = useState<{ map: DailyMap; date: string } | null>(null)
  const [pendingSave, setPendingSave] = useState<{ map: DailyMap; date: string } | null>(null)
  const [savingMap, setSavingMap] = useState(false)
  const mood = destinationMood(trip_info.destination)
  const activeDayIndex = Math.max(
    0,
    itinerary.findIndex((day) => day.id === activeDay),
  )
  const selectedDay = itinerary[activeDayIndex]

  function toggleSchedule(id: string) {
    setExpandedSchedules((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function toggleDayMap(dayId: string) {
    setOpenMapDays((current) => {
      const next = new Set(current)
      if (next.has(dayId)) next.delete(dayId)
      else next.add(dayId)
      return next
    })
  }

  function savePendingMap() {
    if (!pendingSave?.map.image_url) return
    setSavingMap(true)
    const imageUrl = resolveAssetUrl(pendingSave.map.image_url)
    void saveTravelImage(
      imageUrl,
      `${pendingSave.date}-${pendingSave.map.title}-游玩地图`,
      "每日游玩地图",
    )
      .then(() => {
        setPendingSave(null)
        toast("地图已保存", "success")
      })
      .catch(() => toast("地图保存失败，请稍后重试", "error"))
      .finally(() => setSavingMap(false))
  }

  return (
    <>
      <article className={`itinerary-experience itinerary-mood-${mood}`}>
      <header className="itinerary-hero">
        <div className="itinerary-hero-light" aria-hidden />
        <div className="itinerary-hero-depth" aria-hidden />
        <div className="itinerary-hero-content">
          <p className="itinerary-destination">
            <MapPin className="size-3.5" aria-hidden />
            {trip_info.destination}
          </p>
          <p className="itinerary-date">
            <CalendarDays className="size-4" aria-hidden />
            {trip_info.date_label} · {itinerary.length} 天
          </p>
        </div>
      </header>

      {advisories && advisories.length > 0 ? (
        <aside className="itinerary-advisories" role="status">
          <p className="itinerary-advisories-title">
            <TriangleAlert className="size-3.5" aria-hidden />
            出行前请核对
          </p>
          <ul>
            {advisories.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </aside>
      ) : null}

      {(preparations.length > 0 ||
        bookings.length > 0 ||
        food_recommendations.length > 0) && (
        <div className="itinerary-front-guides">
          {(preparations.length > 0 || bookings.length > 0) && (
            <GuideAccordion
              title="预订指南"
              summary={`${preparations.length + bookings.length} 项出发前信息`}
              icon={Ticket}
              open={bookingOpen}
              onToggle={() => setBookingOpen((open) => !open)}
            >
              {preparations.map((item) => (
                <CommandItem
                  key={`${item.category}-${item.items}`}
                  icon={Luggage}
                  title={item.category}
                  content={item.items}
                />
              ))}
              {bookings.map((item) => (
                <CommandItem
                  key={`${item.type}-${item.details}`}
                  icon={Ticket}
                  title={item.type}
                  content={item.details}
                />
              ))}
            </GuideAccordion>
          )}

          {food_recommendations.length > 0 && (
            <GuideAccordion
              title="美食推荐"
              summary={`${food_recommendations.length} 项在地风味`}
              icon={Utensils}
              open={foodOpen}
              onToggle={() => setFoodOpen((open) => !open)}
              tone="food"
            >
              <div className="itinerary-food-list">
                {food_recommendations.map((food) => (
                  <span key={food}>{food}</span>
                ))}
              </div>
            </GuideAccordion>
          )}
        </div>
      )}

      {itinerary.length > 0 && (
        <nav
          className="itinerary-day-nav no-scrollbar"
          aria-label="按日期浏览行程"
          role="tablist"
        >
          {itinerary.map((day, index) => {
            const active = activeDayIndex === index
            return (
              <button
                key={day.id}
                type="button"
                role="tab"
                aria-selected={active}
                aria-controls={`trip-${day.id}`}
                className={active ? "is-active" : undefined}
                onClick={() => setActiveDay(day.id)}
              >
                <span>D{index + 1}</span>
                <strong>{PERIOD_ICONS[day.schedules[0]?.time_period] || "✦"}</strong>
                <small>{day.date.slice(5).replace("-", "/")}</small>
              </button>
            )
          })}
        </nav>
      )}

      <div className="itinerary-body">
        {selectedDay ? (
          <section
            id={`trip-${selectedDay.id}`}
            key={selectedDay.id}
            className="itinerary-day"
            role="tabpanel"
          >
            <div className="itinerary-day-heading">
              <span>D{activeDayIndex + 1}</span>
              <div>
                <p>{selectedDay.date}</p>
                <h2>{selectedDay.title || `第 ${activeDayIndex + 1} 天`}</h2>
              </div>
            </div>

            <ol className="itinerary-light-route">
              {selectedDay.schedules.map((schedule, scheduleIndex) => (
                <ScheduleCard
                  key={schedule.id}
                  schedule={schedule}
                  expanded={expandedSchedules.has(schedule.id)}
                  onToggle={() => toggleSchedule(schedule.id)}
                  index={scheduleIndex}
                  current={scheduleIndex === 0}
                />
              ))}
            </ol>

            {selectedDay.daily_maps?.length ? (
              <DailyMapCard
                maps={selectedDay.daily_maps}
                date={selectedDay.date}
                open={openMapDays.has(selectedDay.id)}
                onToggle={() => toggleDayMap(selectedDay.id)}
                onPreview={(map) => setPreviewMap({ map, date: selectedDay.date })}
                onRequestSave={(map) => setPendingSave({ map, date: selectedDay.date })}
              />
            ) : null}
          </section>
        ) : null}
      </div>
      </article>
      <MapLightbox
        selection={previewMap}
        onClose={() => setPreviewMap(null)}
        onRequestSave={(selection) => setPendingSave(selection)}
        registerBackHandler={registerBackHandler}
      />
      <ConfirmDialog
        open={Boolean(pendingSave)}
        title="保存这张游玩地图？"
        description="地图将保存到手机系统相册；网页端会下载原图。"
        icon={<Download className="size-7 text-primary" aria-hidden />}
        onClose={() => {
          if (!savingMap) setPendingSave(null)
        }}
        actions={[
          {
            label: savingMap ? "正在保存…" : "保存图片",
            onClick: savePendingMap,
          },
          {
            label: "取消",
            variant: "ghost",
            onClick: () => setPendingSave(null),
          },
        ]}
      />
    </>
  )
}

const LONG_PRESS_MS = 650
const LONG_PRESS_MOVE_TOLERANCE = 12

function DailyMapCard({
  maps,
  date,
  open,
  onToggle,
  onPreview,
  onRequestSave,
}: {
  maps: DailyMap[]
  date: string
  open: boolean
  onToggle: () => void
  onPreview: (map: DailyMap) => void
  onRequestSave: (map: DailyMap) => void
}) {
  const [activeIndex, setActiveIndex] = useState(0)
  const activeMap = maps[Math.min(activeIndex, maps.length - 1)]
  const pointCount = maps.reduce((total, map) => total + map.points.length, 0)

  return (
    <section className={`daily-map-card ${open ? "is-open" : ""}`}>
      <button
        type="button"
        className="daily-map-toggle"
        aria-expanded={open}
        onClick={onToggle}
      >
        <span className="daily-map-toggle-icon"><MapIcon className="size-4" aria-hidden /></span>
        <span>
          <strong>当日游玩地图</strong>
          <small>{maps.length > 1 ? `${maps.length} 个游玩区域 · ` : ""}{pointCount} 个地点</small>
        </span>
        <ChevronDown className={open ? "is-open size-4" : "size-4"} aria-hidden />
      </button>

      {open ? (
        <div className="daily-map-content">
          {maps.length > 1 ? (
            <div className="daily-map-tabs" role="tablist" aria-label="切换当日游玩区域">
              {maps.map((map, index) => (
                <button
                  key={map.id}
                  type="button"
                  role="tab"
                  aria-selected={activeIndex === index}
                  className={activeIndex === index ? "is-active" : ""}
                  onClick={() => setActiveIndex(index)}
                >
                  {map.title}
                </button>
              ))}
            </div>
          ) : (
            <p className="daily-map-area">{activeMap.title}</p>
          )}
          <DailyMapPanel
            map={activeMap}
            onPreview={() => onPreview(activeMap)}
            onRequestSave={() => onRequestSave(activeMap)}
          />
        </div>
      ) : null}
    </section>
  )
}

function DailyMapPanel({
  map,
  onPreview,
  onRequestSave,
}: {
  map: DailyMap
  onPreview: () => void
  onRequestSave: () => void
}) {
  const [failedImageId, setFailedImageId] = useState<string | null>(null)
  const longPressTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pressStartRef = useRef<{ x: number; y: number } | null>(null)
  const didLongPressRef = useRef(false)
  const imageUrl = map.image_url ? resolveAssetUrl(map.image_url) : ""
  const canShowImage = map.status === "ready" && Boolean(imageUrl) && failedImageId !== map.id

  useEffect(
    () => () => {
      if (longPressTimerRef.current) clearTimeout(longPressTimerRef.current)
    },
    [],
  )

  function cancelLongPress() {
    if (longPressTimerRef.current) clearTimeout(longPressTimerRef.current)
    longPressTimerRef.current = null
    pressStartRef.current = null
  }

  function startLongPress(event: ReactPointerEvent<HTMLButtonElement>) {
    if (event.pointerType === "mouse" && event.button !== 0) return
    cancelLongPress()
    didLongPressRef.current = false
    pressStartRef.current = { x: event.clientX, y: event.clientY }
    longPressTimerRef.current = setTimeout(() => {
      longPressTimerRef.current = null
      pressStartRef.current = null
      didLongPressRef.current = true
      if ("vibrate" in navigator) navigator.vibrate(30)
      onRequestSave()
    }, LONG_PRESS_MS)
  }

  function moveLongPress(event: ReactPointerEvent<HTMLButtonElement>) {
    const start = pressStartRef.current
    if (!start) return
    if (
      Math.abs(event.clientX - start.x) > LONG_PRESS_MOVE_TOLERANCE ||
      Math.abs(event.clientY - start.y) > LONG_PRESS_MOVE_TOLERANCE
    ) {
      cancelLongPress()
    }
  }

  return (
    <div className="daily-map-panel">
      {canShowImage ? (
        <button
          type="button"
          className="daily-map-image-button"
          aria-label={`放大查看${map.title}游玩地图`}
          onClick={() => {
            if (didLongPressRef.current) {
              didLongPressRef.current = false
              return
            }
            onPreview()
          }}
          onContextMenu={(event) => event.preventDefault()}
          onPointerDown={startLongPress}
          onPointerMove={moveLongPress}
          onPointerUp={cancelLongPress}
          onPointerCancel={cancelLongPress}
          onPointerLeave={cancelLongPress}
        >
          {/* Direct image rendering preserves the original cached map for zoom and save. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={imageUrl}
            alt={`${map.title}当天酒店与景点相对位置图`}
            draggable={false}
            onError={() => setFailedImageId(map.id)}
          />
          <span>点击放大</span>
        </button>
      ) : (
        <div className="daily-map-fallback" role="img" aria-label="地图图片暂时不可用">
          <MapIcon className="size-5" aria-hidden />
          <span>地图暂时不可用</span>
        </div>
      )}

      <div className="daily-map-legend" aria-label="地图地点图例">
        {map.points.map((point) => (
          <span key={`${point.schedule_id}-${point.marker}`}>
            <b className={point.kind === "hotel" ? "is-hotel" : ""}>{point.marker}</b>
            {point.name}
          </span>
        ))}
      </div>

      {map.legs.length ? (
        <div className="daily-map-transport" aria-label="地点间交通方式">
          {map.legs.map((leg) => (
            <span key={`${leg.origin_marker}-${leg.destination_marker}`}>
              <b>{leg.origin_marker}</b>
              <i aria-hidden>→</i>
              {leg.transport_text}
              <i aria-hidden>→</i>
              <b>{leg.destination_marker}</b>
            </span>
          ))}
        </div>
      ) : null}
      <p className="daily-map-note">{map.line_note}{canShowImage ? " · 长按保存" : ""}</p>
    </div>
  )
}

function MapLightbox({
  selection,
  onClose,
  onRequestSave,
  registerBackHandler,
}: {
  selection: { map: DailyMap; date: string } | null
  onClose: () => void
  onRequestSave: (selection: { map: DailyMap; date: string }) => void
  registerBackHandler: (handler: () => void) => () => void
}) {
  useEffect(() => {
    if (!selection) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose()
    }
    document.addEventListener("keydown", onKeyDown)
    const unregister = registerBackHandler(onClose)
    return () => {
      document.removeEventListener("keydown", onKeyDown)
      unregister()
    }
  }, [onClose, registerBackHandler, selection])

  if (!selection?.map.image_url) return null
  const imageUrl = resolveAssetUrl(selection.map.image_url)
  return (
    <div className="daily-map-lightbox" role="presentation" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label={`${selection.map.title}游玩地图大图`} onClick={(event) => event.stopPropagation()}>
        <div className="daily-map-lightbox-bar">
          <p><strong>{selection.map.title}</strong><span>{selection.date}</span></p>
          <span>
            <button type="button" onClick={() => onRequestSave(selection)} aria-label="保存地图">
              <Download className="size-4" aria-hidden />
            </button>
            <button type="button" onClick={onClose} aria-label="关闭大图">
              <X className="size-5" aria-hidden />
            </button>
          </span>
        </div>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={imageUrl} alt={`${selection.map.title}当天酒店与景点相对位置大图`} draggable={false} />
      </div>
    </div>
  )
}

function ScheduleCard({
  schedule,
  expanded,
  onToggle,
  index,
  current,
}: {
  schedule: Schedule
  expanded: boolean
  onToggle: () => void
  index: number
  current: boolean
}) {
  const hasDetails = Boolean(
    schedule.transport ||
      schedule.note ||
      schedule.duration_minutes ||
      schedule.distance_km ||
      schedule.booking_required,
  )
  const placeName = schedule.place_name || schedule.activity
  const mapHref = schedule.location
    ? `https://uri.amap.com/marker?position=${encodeURIComponent(schedule.location)}&name=${encodeURIComponent(placeName)}`
    : null
  const detailLabel =
    schedule.action?.type === "alternative"
      ? schedule.action.label || "查看备选"
      : schedule.action?.type === "booking" || schedule.booking_required
        ? schedule.action?.label || "去确认预约"
        : schedule.action?.type === "details"
          ? schedule.action.label
          : "展开交通"
  const periodClass =
    schedule.time_period === "晚上"
      ? "is-evening"
      : schedule.time_period === "下午"
        ? "is-afternoon"
        : schedule.time_period === "中午"
          ? "is-noon"
          : "is-morning"

  return (
    <li
      className={`itinerary-stop ${periodClass} ${current ? "is-current" : ""}`}
      style={{ "--schedule-index": index } as CSSProperties & Record<"--schedule-index", number>}
    >
      <span className="itinerary-node" aria-hidden />
      <div className="itinerary-card">
        <div className="itinerary-card-meta">
          <time>
            {schedule.time_period ? <span>{schedule.time_period}</span> : null}
            {schedule.start_time ? (
              <b>
                {schedule.start_time}
                {schedule.end_time ? `–${schedule.end_time}` : ""}
              </b>
            ) : null}
          </time>
          {schedule.travel_minutes ? (
            <span>
              <Navigation className="size-3" aria-hidden />
              通勤 {schedule.travel_minutes} 分钟
            </span>
          ) : null}
          {/* 后端核对未通过的那几条会标成 unverified。这个提示不能藏在“展开详情”
              里——按错的时刻去买票，是看不到才会犯的错。 */}
          {schedule.fact_status === "unverified" ? (
            <span className="itinerary-unconfirmed">
              <TriangleAlert className="size-3" aria-hidden />
              时刻待确认
            </span>
          ) : null}
        </div>
        <h3>
          <MapPin className="size-4" aria-hidden />
          {placeName}
        </h3>
        {schedule.place_name && schedule.activity !== schedule.place_name && (
          <p className="itinerary-activity">{schedule.activity}</p>
        )}
        {schedule.tags?.length ? (
          <div className="itinerary-tags">
            {schedule.tags.slice(0, 4).map((tag) => (
              <span key={tag}>{tag}</span>
            ))}
          </div>
        ) : null}

        {expanded && hasDetails && (
          <div className="itinerary-card-details">
            {schedule.transport && (
              <p>
                <Route className="size-4" aria-hidden />
                {schedule.transport}
              </p>
            )}
            {(schedule.distance_km || schedule.duration_minutes) && (
              <p>
                <Footprints className="size-4" aria-hidden />
                {schedule.distance_km ? `${schedule.distance_km} 公里` : ""}
                {schedule.distance_km && schedule.duration_minutes ? " · " : ""}
                {schedule.duration_minutes ? `建议停留 ${schedule.duration_minutes} 分钟` : ""}
              </p>
            )}
            {schedule.note && <p className="itinerary-note">{schedule.note}</p>}
          </div>
        )}

        <div className="itinerary-card-actions">
          {mapHref && (
            <a href={mapHref} target="_blank" rel="noreferrer">
              <MapIcon className="size-3.5" aria-hidden />
              {schedule.action?.type === "map" ? schedule.action.label : "查看路线"}
            </a>
          )}
          {hasDetails && (
            <button type="button" onClick={onToggle}>
              {schedule.booking_required ? <Ticket className="size-3.5" /> : <Route className="size-3.5" />}
              {expanded ? "收起详情" : detailLabel}
            </button>
          )}
        </div>
      </div>
    </li>
  )
}

function GuideAccordion({
  title,
  summary,
  icon: Icon,
  open,
  onToggle,
  tone,
  children,
}: {
  title: string
  summary: string
  icon: React.ComponentType<{ className?: string }>
  open: boolean
  onToggle: () => void
  tone?: "food"
  children: React.ReactNode
}) {
  return (
    <section className={`itinerary-command ${tone === "food" ? "is-food" : ""}`}>
      <button
        type="button"
        className="itinerary-command-toggle"
        onClick={onToggle}
        aria-expanded={open}
      >
        <span>
          <Icon className="size-5" aria-hidden />
          <span>
            <strong>{title}</strong>
            <small>{summary}</small>
          </span>
        </span>
        <ChevronDown className={open ? "is-open size-5" : "size-5"} aria-hidden />
      </button>
      {open ? <div className="itinerary-command-content">{children}</div> : null}
    </section>
  )
}

function CommandItem({
  icon: Icon,
  title,
  content,
}: {
  icon: React.ComponentType<{ className?: string }>
  title: string
  content: string
}) {
  return (
    <div>
      <Icon className="size-4" aria-hidden />
      <p>
        <strong>{title}</strong>
        <span>{content}</span>
      </p>
    </div>
  )
}
