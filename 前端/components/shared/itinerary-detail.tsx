"use client"

import type { ItineraryData } from "@/types"
import {
  Calendar,
  Luggage,
  Ticket,
  Utensils,
  Clock,
  MapPin,
  ArrowRight,
  NotebookPen,
} from "lucide-react"

function SectionTitle({
  icon: Icon,
  children,
}: {
  icon: React.ComponentType<{ className?: string }>
  children: React.ReactNode
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="flex size-7 items-center justify-center rounded-full bg-secondary text-primary ring-1 ring-primary/10">
        <Icon className="h-4 w-4" />
      </span>
      <h3 className="font-display text-sm font-bold tracking-wide text-foreground">{children}</h3>
    </div>
  )
}

const TIME_PERIOD_STYLE: Record<string, string> = {
  上午: "bg-[var(--color-chip-morning)] text-[var(--color-chip-morning-fg)]",
  下午: "bg-[var(--color-chip-afternoon)] text-[var(--color-chip-afternoon-fg)]",
  晚上: "bg-[var(--color-chip-evening)] text-[var(--color-chip-evening-fg)]",
}

export function ItineraryDetail({ data }: { data: ItineraryData }) {
  const { trip_info, preparations, bookings, food_recommendations, itinerary } = data

  return (
    <div className="flex flex-col gap-6">
      {/* 行程概览 */}
      <section className="overflow-hidden rounded-[1.25rem] border border-white/12 bg-[#07516c] p-4 text-white shadow-[0_18px_36px_-22px_rgba(6,45,72,0.9)]">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="flex items-center gap-1 text-xs font-medium text-[#73d4df]">
              <MapPin className="h-3.5 w-3.5" /> 目的地
            </p>
            <p className="mt-0.5 truncate font-display text-xl font-black text-white">{trip_info.destination}</p>
            <p className="mt-1 flex items-center gap-1 font-editorial text-xs font-medium text-white/70">
              <Calendar className="h-3.5 w-3.5" /> {trip_info.date_label}
            </p>
          </div>
        </div>
        {/* 快速概览统计 */}
        <div className="mt-3 grid grid-cols-3 gap-2">
          {[
            { label: "天行程", value: itinerary.length },
            { label: "项预订", value: bookings.length },
            { label: "道美食", value: food_recommendations.length },
          ].map((stat) => (
            <div
              key={stat.label}
              className="rounded-xl bg-white/8 px-2 py-2 text-center ring-1 ring-white/12"
            >
              <p className="text-lg font-bold leading-none text-[#73d4df]">{stat.value}</p>
              <p className="mt-1 text-[11px] text-white/66">{stat.label}</p>
            </div>
          ))}
        </div>
      </section>

      {/* 出行准备 */}
      {preparations.length > 0 && (
        <section className="flex flex-col gap-3">
          <SectionTitle icon={Luggage}>出行准备</SectionTitle>
          <ul className="flex flex-col gap-2">
            {preparations.map((p, i) => (
              <li
                key={i}
                className="rounded-2xl border border-[#0a3850]/10 bg-card/96 px-3.5 py-3 text-sm shadow-[var(--shadow-surface)]"
              >
                <span className="font-medium text-foreground">{p.category}</span>
                <span className="mt-0.5 block text-muted-foreground leading-relaxed">{p.items}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* 预订事项 */}
      {bookings.length > 0 && (
        <section className="flex flex-col gap-3">
          <SectionTitle icon={Ticket}>预订事项</SectionTitle>
          <ul className="flex flex-col gap-2">
            {bookings.map((b, i) => (
              <li
                key={i}
                className="flex items-start gap-2.5 rounded-2xl border border-[#0a3850]/10 bg-card/96 px-3.5 py-3 text-sm shadow-[var(--shadow-surface)]"
              >
                <span className="mt-0.5 shrink-0 rounded-md bg-accent/15 px-2 py-0.5 text-xs font-medium text-accent-strong">
                  {b.type}
                </span>
                <span className="text-muted-foreground leading-relaxed">{b.details}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* 美食推荐 */}
      {food_recommendations.length > 0 && (
        <section className="flex flex-col gap-3">
          <SectionTitle icon={Utensils}>美食推荐</SectionTitle>
          <div className="flex flex-wrap gap-2">
            {food_recommendations.map((f, i) => (
              <span
                key={i}
                className="rounded-full border border-[#0a3850]/12 bg-card/94 px-3 py-1.5 font-editorial text-xs font-medium text-foreground shadow-sm"
              >
                {f}
              </span>
            ))}
          </div>
        </section>
      )}

      {/* 每日行程 */}
      {itinerary.length > 0 && (
        <section className="flex flex-col gap-4">
          <SectionTitle icon={Calendar}>每日行程</SectionTitle>
          <div className="flex flex-col gap-4">
            {itinerary.map((day, dayIndex) => (
              <div key={day.id} className="relative">
                <div className="flex items-center gap-2">
                  <span className="flex h-6 min-w-6 items-center justify-center rounded-full bg-primary px-2 text-xs font-bold text-primary-foreground">
                    D{dayIndex + 1}
                  </span>
                  <div className="flex flex-col">
                    <span className="text-sm font-semibold text-foreground">
                      {day.title ?? `第 ${dayIndex + 1} 天`}
                    </span>
                    <span className="text-xs text-muted-foreground">{day.date}</span>
                  </div>
                </div>

                <ol className="mt-3 flex flex-col gap-2.5 border-l-2 border-dashed border-border pl-4">
                  {day.schedules.map((s) => (
                    <li key={s.id} className="relative">
                      <span className="absolute -left-[1.32rem] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-primary bg-background" />
                      <div className="rounded-2xl border border-[#0a3850]/10 bg-card/96 p-3.5 shadow-[var(--shadow-surface)]">
                        <div className="flex flex-wrap items-center gap-2">
                          <span
                            className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${
                              TIME_PERIOD_STYLE[s.time_period] ?? "bg-secondary text-secondary-foreground"
                            }`}
                          >
                            {s.time_period}
                          </span>
                          {(s.start_time || s.end_time) && (
                            <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
                              <Clock className="h-3 w-3" />
                              {s.start_time}
                              {s.end_time ? ` - ${s.end_time}` : ""}
                            </span>
                          )}
                        </div>
                        <p className="mt-1.5 flex items-start gap-1.5 text-sm font-medium text-foreground">
                          <MapPin className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
                          {s.activity}
                        </p>
                        {s.transport && (
                          <p className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                            <ArrowRight className="h-3 w-3" />
                            {s.transport}
                          </p>
                        )}
                        {s.note && (
                          <p className="mt-1 flex items-start gap-1.5 rounded-lg bg-secondary px-2 py-1.5 text-xs text-secondary-foreground">
                            <NotebookPen className="mt-0.5 h-3 w-3 shrink-0" />
                            {s.note}
                          </p>
                        )}
                      </div>
                    </li>
                  ))}
                </ol>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
