import type { Schedule } from "@/types"

export type ScheduleKind = "hotel" | "attraction" | "dining" | "transport" | "shopping" | "other"

const HOTEL_TAGS = new Set(["住宿"])
const DINING_TAGS = new Set(["美食", "餐饮"])
const TRANSPORT_TAGS = new Set(["大交通", "换乘"])
const SHOPPING_TAGS = new Set(["购物"])
const ATTRACTION_TAGS = new Set([
  "历史",
  "文化",
  "景区",
  "风景",
  "自然",
  "滨海",
  "博物馆",
  "公园",
  "广场",
  "建筑",
  "红色",
  "演出",
  "古镇",
  "街区",
])

const HOTEL_NAME = /酒店|宾馆|客栈|民宿|旅馆|度假村/u
const HOTEL_ACTION = /入住|退房|住宿/u
const TRANSPORT_HUB = /机场|航站楼|火车站|高铁站|客运站|汽车站|码头|港口/u
const WALKING_STREET = /步行街|古街|老街|商业街|文化街|美食街/u
const DINING_NAME = /餐厅|饭店|食馆|小吃|咖啡|茶馆|烧烤店/u
const SHOPPING_NAME = /商场|购物中心|免税店|超市|便利店/u

export function classifySchedule(schedule: Schedule): ScheduleKind {
  if (schedule.map_role === "hotel" || schedule.map_role === "attraction") {
    return schedule.map_role
  }

  const tags = new Set((schedule.tags || []).map((tag) => tag.trim()).filter(Boolean))
  const text = searchable(schedule)

  if (hasTag(tags, TRANSPORT_TAGS)) return "transport"
  if (hasTag(tags, HOTEL_TAGS)) return "hotel"
  if (hasTag(tags, ATTRACTION_TAGS) || isWalkingStreet(text)) return "attraction"
  if (hasTag(tags, DINING_TAGS)) return "dining"
  if (hasTag(tags, SHOPPING_TAGS)) return "shopping"
  if (TRANSPORT_HUB.test(text)) return "transport"
  if (HOTEL_NAME.test(text) && HOTEL_ACTION.test(text)) return "hotel"
  if (DINING_NAME.test(text)) return "dining"
  if (SHOPPING_NAME.test(text)) return "shopping"
  return "other"
}

function searchable(schedule: Schedule): string {
  return [schedule.place_name, schedule.activity].filter(Boolean).join(" ")
}

function isWalkingStreet(text: string): boolean {
  if (WALKING_STREET.test(text)) return true
  return text.includes("大街") && !DINING_NAME.test(text)
}

function hasTag(tags: Set<string>, allowed: Set<string>): boolean {
  for (const tag of tags) {
    if (allowed.has(tag)) return true
  }
  return false
}
