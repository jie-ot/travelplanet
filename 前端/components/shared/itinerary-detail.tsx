"use client"

import { useState, type CSSProperties } from "react"
import {
  CalendarDays,
  ChevronDown,
  Footprints,
  Luggage,
  Map as MapIcon,
  MapPin,
  Navigation,
  Route,
  Ticket,
  Utensils,
} from "lucide-react"
import type { ItineraryData, Schedule } from "@/types"

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
  const { trip_info, preparations, bookings, food_recommendations, itinerary } = data
  const [activeDay, setActiveDay] = useState(itinerary[0]?.id ?? "")
  const [expandedSchedules, setExpandedSchedules] = useState<Set<string>>(() => new Set())
  const [bookingOpen, setBookingOpen] = useState(false)
  const [foodOpen, setFoodOpen] = useState(false)
  const summary = data.experience_summary
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

  return (
    <article className={`itinerary-experience itinerary-mood-${mood}`}>
      <header className="itinerary-hero">
        <div className="itinerary-hero-light" aria-hidden />
        <div className="itinerary-hero-depth" aria-hidden />
        <div className="itinerary-hero-content">
          <p className="itinerary-destination">
            <MapPin className="size-3.5" aria-hidden />
            {trip_info.destination}
          </p>
          <h1>{summary?.tripTheme || `${trip_info.destination}沉浸之旅`}</h1>
          <p className="itinerary-date">
            <CalendarDays className="size-4" aria-hidden />
            {trip_info.date_label} · {itinerary.length} 天
          </p>
        </div>
      </header>

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
          </section>
        ) : null}
      </div>
    </article>
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
