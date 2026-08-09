import type { ItineraryData, Schedule } from "@/types"

const WIDTH = 1080
const PADDING = 72
const CONTENT_WIDTH = WIDTH - PADDING * 2
const MAX_HEIGHT = 28_000
const FONT_FAMILY = '"Microsoft YaHei", "PingFang SC", sans-serif'

type Command =
  | { type: "rect"; x: number; y: number; width: number; height: number; radius: number; color: string }
  | { type: "line"; x1: number; y1: number; x2: number; y2: number; width: number; color: string }
  | { type: "circle"; x: number; y: number; radius: number; color: string }
  | {
      type: "text"
      lines: string[]
      x: number
      y: number
      font: string
      color: string
      lineHeight: number
    }

export async function renderItineraryImage(data: ItineraryData): Promise<Blob> {
  await document.fonts?.ready

  const measureCanvas = document.createElement("canvas")
  const measure = measureCanvas.getContext("2d")
  if (!measure) throw new Error("当前浏览器无法生成行程长图")

  const commands: Command[] = []
  const font = (size: number, weight = 500) => `${weight} ${size}px ${FONT_FAMILY}`
  let y = 0

  commands.push({ type: "rect", x: 0, y: 0, width: WIDTH, height: 310, radius: 0, color: "#073b56" })
  commands.push({ type: "circle", x: 915, y: 58, radius: 155, color: "#0b7890" })
  commands.push({ type: "circle", x: 1005, y: 225, radius: 105, color: "#ef754b" })
  commands.push({
    type: "text",
    lines: ["TRAVEL PLANET · 核心行程"],
    x: PADDING,
    y: 64,
    font: font(24, 700),
    color: "#8fe3df",
    lineHeight: 34,
  })
  commands.push({
    type: "text",
    lines: wrapText(measure, data.trip_info.destination, CONTENT_WIDTH - 110, font(58, 900)),
    x: PADDING,
    y: 116,
    font: font(58, 900),
    color: "#fffaf1",
    lineHeight: 72,
  })
  commands.push({
    type: "text",
    lines: [`${data.trip_info.date_label} · ${data.itinerary.length} 天`],
    x: PADDING,
    y: 244,
    font: font(28, 600),
    color: "#d6eeec",
    lineHeight: 40,
  })
  y = 356

  if (data.advisories?.length) {
    const advisoryLines = data.advisories
      .slice(0, 4)
      .flatMap((item) => wrapText(measure, `• ${item}`, CONTENT_WIDTH - 64, font(23, 500)))
    const boxHeight = 78 + advisoryLines.length * 34
    commands.push({ type: "rect", x: PADDING, y, width: CONTENT_WIDTH, height: boxHeight, radius: 28, color: "#fff0d8" })
    commands.push({ type: "text", lines: ["出行前请核对"], x: PADDING + 32, y: y + 28, font: font(27, 800), color: "#9a5727", lineHeight: 38 })
    commands.push({ type: "text", lines: advisoryLines, x: PADDING + 32, y: y + 76, font: font(23, 500), color: "#704b32", lineHeight: 34 })
    y += boxHeight + 34
  }

  for (const [dayIndex, day] of data.itinerary.entries()) {
    commands.push({ type: "rect", x: PADDING, y, width: CONTENT_WIDTH, height: 86, radius: 24, color: "#dff3f1" })
    commands.push({ type: "circle", x: PADDING + 46, y: y + 43, radius: 28, color: "#07899b" })
    commands.push({ type: "text", lines: [`D${dayIndex + 1}`], x: PADDING + 29, y: y + 27, font: font(21, 800), color: "#ffffff", lineHeight: 30 })
    commands.push({ type: "text", lines: [day.date], x: PADDING + 92, y: y + 17, font: font(21, 600), color: "#4e747d", lineHeight: 30 })
    commands.push({
      type: "text",
      lines: wrapText(measure, day.title || `第 ${dayIndex + 1} 天`, CONTENT_WIDTH - 132, font(31, 800)).slice(0, 1),
      x: PADDING + 92,
      y: y + 45,
      font: font(31, 800),
      color: "#073b56",
      lineHeight: 40,
    })
    y += 118

    for (const [scheduleIndex, schedule] of day.schedules.entries()) {
      y = addSchedule(commands, measure, schedule, y, scheduleIndex === day.schedules.length - 1, font)
    }
    y += 34
  }

  const guideItems = [
    ...data.preparations.slice(0, 3).map((item) => `${item.category}：${item.items}`),
    ...data.bookings.slice(0, 3).map((item) => `${item.type}：${item.details}`),
  ]
  if (guideItems.length) {
    const guideLines = guideItems.flatMap((item) => wrapText(measure, `• ${item}`, CONTENT_WIDTH - 64, font(22, 500)))
    const guideHeight = 78 + guideLines.length * 33
    commands.push({ type: "rect", x: PADDING, y, width: CONTENT_WIDTH, height: guideHeight, radius: 28, color: "#edf4f4" })
    commands.push({ type: "text", lines: ["预订与准备"], x: PADDING + 32, y: y + 27, font: font(27, 800), color: "#073b56", lineHeight: 38 })
    commands.push({ type: "text", lines: guideLines, x: PADDING + 32, y: y + 74, font: font(22, 500), color: "#365966", lineHeight: 33 })
    y += guideHeight + 42
  }

  commands.push({ type: "line", x1: PADDING, y1: y, x2: WIDTH - PADDING, y2: y, width: 2, color: "#bdd5d6" })
  commands.push({ type: "text", lines: ["旅行星球 · 让每一段旅程清晰可执行"], x: PADDING, y: y + 30, font: font(21, 600), color: "#608087", lineHeight: 32 })
  const height = Math.ceil(y + 104)
  if (height > MAX_HEIGHT) throw new Error("行程内容过长，暂时无法生成单张长图")

  const canvas = document.createElement("canvas")
  canvas.width = WIDTH
  canvas.height = height
  const context = canvas.getContext("2d")
  if (!context) throw new Error("当前浏览器无法生成行程长图")
  context.fillStyle = "#f7f3eb"
  context.fillRect(0, 0, WIDTH, height)
  for (const command of commands) drawCommand(context, command)

  return await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error("行程长图生成失败"))),
      "image/png",
    )
  })
}

function addSchedule(
  commands: Command[],
  measure: CanvasRenderingContext2D,
  schedule: Schedule,
  y: number,
  last: boolean,
  font: (size: number, weight?: number) => string,
): number {
  const textX = PADDING + 102
  const textWidth = CONTENT_WIDTH - 126
  const time = [schedule.start_time, schedule.end_time].filter(Boolean).join("–")
  const meta = [time, schedule.time_period].filter(Boolean).join(" · ") || "时间灵活"
  const place = schedule.place_name || schedule.activity
  const activity = schedule.place_name && schedule.activity !== schedule.place_name ? schedule.activity : ""
  const placeLines = wrapText(measure, place, textWidth, font(28, 800))
  const activityLines = activity ? wrapText(measure, activity, textWidth, font(22, 500)) : []
  const transportLines = schedule.transport
    ? wrapText(measure, `交通：${schedule.transport}`, textWidth, font(20, 500)).slice(0, 2)
    : []
  const blockHeight = 38 + placeLines.length * 38 + activityLines.length * 32 + transportLines.length * 30 + 24

  commands.push({ type: "circle", x: PADDING + 42, y: y + 20, radius: 10, color: schedule.fact_status === "unverified" ? "#ed7c4b" : "#0795a1" })
  if (!last) commands.push({ type: "line", x1: PADDING + 42, y1: y + 32, x2: PADDING + 42, y2: y + blockHeight + 12, width: 4, color: "#bed9d8" })
  commands.push({ type: "text", lines: [meta], x: textX, y, font: font(20, 700), color: "#668087", lineHeight: 30 })
  commands.push({ type: "text", lines: placeLines, x: textX, y: y + 34, font: font(28, 800), color: "#123f50", lineHeight: 38 })
  let textY = y + 34 + placeLines.length * 38
  if (activityLines.length) {
    commands.push({ type: "text", lines: activityLines, x: textX, y: textY + 4, font: font(22, 500), color: "#355c68", lineHeight: 32 })
    textY += activityLines.length * 32 + 4
  }
  if (transportLines.length) {
    commands.push({ type: "text", lines: transportLines, x: textX, y: textY + 5, font: font(20, 500), color: "#5a767e", lineHeight: 30 })
  }
  if (schedule.fact_status === "unverified") {
    commands.push({ type: "text", lines: ["时刻待官方确认"], x: WIDTH - PADDING - 178, y, font: font(18, 700), color: "#c85d31", lineHeight: 28 })
  }
  return y + blockHeight
}

function wrapText(
  context: CanvasRenderingContext2D,
  text: string,
  maxWidth: number,
  font: string,
): string[] {
  context.font = font
  const lines: string[] = []
  for (const paragraph of String(text || "").split(/\r?\n/)) {
    let current = ""
    for (const character of Array.from(paragraph)) {
      if (current && context.measureText(current + character).width > maxWidth) {
        lines.push(current)
        current = character
      } else {
        current += character
      }
    }
    if (current || !paragraph) lines.push(current)
  }
  return lines.length ? lines : [""]
}

function drawCommand(context: CanvasRenderingContext2D, command: Command) {
  if (command.type === "text") {
    context.font = command.font
    context.fillStyle = command.color
    context.textBaseline = "top"
    command.lines.forEach((line, index) => context.fillText(line, command.x, command.y + index * command.lineHeight))
    return
  }
  if (command.type === "line") {
    context.beginPath()
    context.moveTo(command.x1, command.y1)
    context.lineTo(command.x2, command.y2)
    context.lineWidth = command.width
    context.strokeStyle = command.color
    context.stroke()
    return
  }
  context.fillStyle = command.color
  context.beginPath()
  if (command.type === "circle") {
    context.arc(command.x, command.y, command.radius, 0, Math.PI * 2)
  } else {
    roundedRect(context, command.x, command.y, command.width, command.height, command.radius)
  }
  context.fill()
}

function roundedRect(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
) {
  const r = Math.min(radius, width / 2, height / 2)
  context.moveTo(x + r, y)
  context.arcTo(x + width, y, x + width, y + height, r)
  context.arcTo(x + width, y + height, x, y + height, r)
  context.arcTo(x, y + height, x, y, r)
  context.arcTo(x, y, x + width, y, r)
  context.closePath()
}
