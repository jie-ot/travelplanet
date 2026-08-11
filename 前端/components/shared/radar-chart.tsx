"use client"

import type { ReportChartPoint } from "@/types"

/**
 * 旅行人格雷达图（5 维固定顺序）。
 * 使用轻量 SVG 绘制，维度：自然探索 / 人文体验 / 美食偏好 / 慢节奏 / 社交意愿。
 */
export function RadarChart({ data, size = 260 }: { data: ReportChartPoint[]; size?: number }) {
  const padX = 42
  const cx = size / 2
  const cy = size / 2
  const radius = size / 2 - 38
  const count = data.length
  const rings = [0.25, 0.5, 0.75, 1]

  const angleFor = (i: number) => (Math.PI * 2 * i) / count - Math.PI / 2
  const pointAt = (i: number, r: number) => ({
    x: cx + r * Math.cos(angleFor(i)),
    y: cy + r * Math.sin(angleFor(i)),
  })

  const valuePoints = data.map((d, i) => pointAt(i, (Math.max(0, Math.min(100, d.value)) / 100) * radius))
  const polygon = valuePoints.map((p) => `${p.x},${p.y}`).join(" ")

  return (
    <svg
      viewBox={`${-padX} 0 ${size + padX * 2} ${size}`}
      className="h-auto w-full max-w-sm"
      role="img"
      aria-label="旅行人格雷达图"
    >
      {/* 网格环 */}
      {rings.map((ring) => (
        <polygon
          key={ring}
          points={data.map((_, i) => {
            const p = pointAt(i, radius * ring)
            return `${p.x},${p.y}`
          }).join(" ")}
          fill="none"
          stroke="var(--border)"
          strokeWidth={1}
          className="persona-radar-grid-ring"
          style={{ animationDelay: `${rings.indexOf(ring) * 70}ms` }}
        />
      ))}

      {/* 轴线 */}
      {data.map((_, i) => {
        const p = pointAt(i, radius)
        return (
          <line
            key={i}
            x1={cx}
            y1={cy}
            x2={p.x}
            y2={p.y}
            stroke="var(--border)"
            strokeWidth={1}
            className="persona-radar-axis"
            style={{ animationDelay: `${120 + i * 45}ms` }}
          />
        )
      })}

      {/* 数据多边形 */}
      <polygon
        points={polygon}
        fill="var(--primary)"
        fillOpacity={0.18}
        stroke="var(--primary)"
        strokeWidth={2}
        className="persona-radar-shape"
      />

      {/* 数据点 */}
      {valuePoints.map((p, i) => (
        <circle
          key={i}
          cx={p.x}
          cy={p.y}
          r={3.25}
          fill="var(--primary)"
          className="persona-radar-point"
          style={{ animationDelay: `${420 + i * 70}ms` }}
        />
      ))}

      {/* 维度标签 + 数值 */}
      {data.map((d, i) => {
        const p = pointAt(i, radius + 27)
        const anchor = Math.abs(p.x - cx) < 4 ? "middle" : p.x > cx ? "start" : "end"
        return (
          <g key={d.dimension}>
            <text
              x={p.x}
              y={p.y + 2}
              textAnchor={anchor}
              className="persona-radar-label fill-foreground"
              style={{ fontSize: 14, fontWeight: 680 }}
            >
              {d.dimension}
            </text>
            <text
              x={p.x}
              y={p.y + 16}
              textAnchor={anchor}
              className="persona-radar-value fill-muted-foreground"
              style={{ fontSize: 11.5, fontWeight: 700 }}
            >
              {String(d.value).padStart(2, "0")}
            </text>
          </g>
        )
      })}
    </svg>
  )
}
