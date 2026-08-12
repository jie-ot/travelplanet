/**
 * 全局类型定义
 *
 * 严格对齐《数据结构与通信接口规范》。
 * - 对外 DTO（PostcardGroup / Postcard / Report / Plan / FileAsset / UploadedPhoto / ReportChartPoint）统一 camelCase
 * - ItineraryData 一支（trip_info / preparations / bookings / itinerary / schedule）统一 snake_case
 * 兼容升级只新增可选字段，不删除或改名旧字段。
 */

/* ---------------- 明信片组 (PostcardGroup) ---------------- */

export interface PostcardGroup {
  id: string
  location: string
  startDate: string | null
  endDate: string | null
  dateLabel: string
  coverImage: string
  postcards: Postcard[]
}

/* ---------------- 单张明信片 (Postcard) ---------------- */

export interface Postcard {
  id: string
  title: string
  imageUrl: string
}

/* ---------------- 前端交给后端的单张照片信息 (UploadedPhoto) ---------------- */

export interface UploadedPhoto {
  assetId: string
  imageUrl: string
  takenAt: string | null
  location: string | null
}

/* ---------------- 文件资源 (FileAsset) ---------------- */

export interface FileAsset {
  id: string
  relativePath: string
  mimeType: string
  sizeBytes: number
  usageType: "upload" | "generated_postcard" | "generated_report_cover" | "system"
  status: "temporary" | "attached" | "deleted"
  refCount: number
}

/* ---------------- 旅行人格报告 (Report) ---------------- */

export interface Report {
  id: string
  location: string
  startDate: string | null
  endDate: string | null
  dateLabel: string
  coverImage: string
  personalitySummary: string
  content: string
  chartData: ReportChartPoint[]
  profileVersion?: number | null
  profileData?: TravelProfileData | null
}

export type VisualTheme =
  | "forest_light"
  | "ocean_blue"
  | "sunset_orange"
  | "museum_gold"
  | "city_neon"
  | "night_purple"
  | "snow_silver"
  | "desert_amber"

export interface ProfileSpectrum {
  id: "environment" | "depth" | "planning" | "social"
  leftLabel: string
  rightLabel: string
  value: number
}

export interface ProfileModule {
  title: string
  content: string
}

export interface TravelProfileData {
  archetypeId: string
  archetypeName: string
  personaCode: string
  slogan: string
  spectrums: ProfileSpectrum[]
  keywords: string[]
  modules: ProfileModule[]
  nextTripInspiration: string
  visualTheme: VisualTheme
}

/* ---------------- 雷达图数据点 (ReportChartPoint) ---------------- */

export type RadarDimension = "自然探索" | "人文体验" | "美食偏好" | "慢节奏" | "社交意愿"

export interface ReportChartPoint {
  dimension: RadarDimension
  value: number
}

/* ---------------- 历史规划 (Plan) ---------------- */

export interface Plan {
  id: string
  location: string
  startDate: string | null
  endDate: string | null
  dateLabel: string
  content: string
  itineraryData: ItineraryData
}

/* ---------------- 多轮旅行规划会话 ---------------- */

export type PlanningPhase = "collecting" | "confirming" | "completed"
export type PlanningModel =
  | "doubao-seed-2.0-pro"
  | "deepseek-v4-flash"
  | "deepseek-v4-pro"

export interface PlanningChatMessage {
  role: "user" | "assistant"
  content: string
  planningModel?: PlanningModel
}

export interface PlanningBrief {
  origin: string | null
  destinations: string[]
  startDate: string | null
  endDate: string | null
  travelerCount: number | null
  budget: string | null
  transportPreference: string | null
  lodgingPreference: string | null
  interests: string[]
  constraints: string[]
  assumptions: string[]
  summary: string
  /** Residual requirements not covered by the structured checklist rows. */
  detailRequirements?: string
}

export interface PlanningChecklistItem {
  key: string
  label: string
  value: string
  status: "ready" | "assumed" | "missing"
  required: boolean
}

export interface PlanningResponse {
  phase: PlanningPhase
  assistantMessage: string
  planningModel: PlanningModel
  brief: PlanningBrief | null
  checklist: PlanningChecklistItem[]
  /** Digest of the exact brief shown in the confirmation checklist. */
  confirmationToken: string | null
  itinerary: ItineraryData | null
}

/** 规划阶段。与后端 planning_progress 的 stage 取值一一对应。 */
export type PlanningProgressStage =
  | "queued"
  | "reading_memory"
  | "collecting_requirements"
  | "prefetching_facts"
  | "understanding_request"
  | "researching"
  | "research_complete"
  | "selecting_facts"
  | "synthesizing"
  | "verifying"
  | "finalizing"
  | "completed"
  | "failed"

export type PlanningProgressPhase =
  | "preparing"
  | "researching"
  | "composing"
  | "verifying"
  | "completed"
  | "failed"

/**
 * 生成过程中的真实后端进度快照（GET /ai/planning/progress）。
 * 规划本身仍是一次阻塞 POST，本快照只供待机动画展示，缺失时不影响出图。
 */
export interface PlanningProgressSnapshot {
  token: string
  stage: PlanningProgressStage
  stageLabel: string
  phase: PlanningProgressPhase
  percent: number
  detail: string | null
  elapsedMs: number
  estimatedTotalMs: number
  estimatedRemainingMs: number
  researchRound: number
  targetRounds: number
  maxRounds: number
  toolCallCount: number
  factCount: number
  repairRound: number
  planningModel: PlanningModel | null
  recentActivities: string[]
  error: string | null
  done: boolean
}

/* ---------------- 旅行记忆展示 (TravelMemory) ---------------- */

export interface TravelMemoryDescription {
  id: string
  icon: string
  title: string
  content: string
  planningHint?: string | null
  sourceLabels?: string[]
  editable: boolean
}

export interface TravelMemoryPlanningPreference {
  key: "transport" | "hotel" | "attractions" | "food" | "pace" | "other"
  label: string
  value: string
  placeholder: string
  helper: string
  editable: boolean
}

export interface TravelMemoryDisplay {
  intro: string | null
  overviewTitle?: string | null
  overviewContent?: string | null
  planningPreferences: TravelMemoryPlanningPreference[]
  memories: TravelMemoryDescription[]
  editable: boolean
  updatedAt: string | null
  version: number
  isEmpty: boolean
}

/* ---------------- 结构化行程数据 (ItineraryData) ---------------- */

export interface ItineraryData {
  trip_info: TripInfo
  preparations: Preparation[]
  bookings: Booking[]
  food_recommendations: string[]
  itinerary: DailyItinerary[]
  experience_summary?: ExperienceSummary | null
  /** Gate-authored caveats for findings that are not tied to a single schedule row. */
  advisories?: string[]
}

export interface ExperienceSummary {
  tripTheme?: string
  pace?: string
  intensity?: number
  highlights?: string[]
  weatherSummary?: string
  personalizationTags?: string[]
}

export interface TripInfo {
  destination: string
  start_date: string
  end_date: string
  date_label: string
}

export interface Preparation {
  category: string
  items: string
}

export interface Booking {
  type: "机票" | "火车票" | "酒店" | "景区门票" | string
  details: string
}

export interface DailyItinerary {
  id: string
  date: string
  title?: string
  schedules: Schedule[]
  daily_maps?: DailyMap[]
}

export interface DailyMapPoint {
  schedule_id: string
  marker: string
  name: string
  location: string
  kind: "hotel" | "attraction"
}

export interface DailyMapLeg {
  origin_marker: string
  destination_marker: string
  transport_text: string
}

export interface DailyMap {
  id: string
  title: string
  image_url?: string | null
  status: "ready" | "unavailable"
  points: DailyMapPoint[]
  legs: DailyMapLeg[]
  line_note: string
}

export interface Schedule {
  id: string
  time_period: string
  start_time?: string | null
  end_time?: string | null
  activity: string
  transport?: string | null
  place_name?: string | null
  location?: string | null
  travel_minutes?: number | null
  distance_km?: number | null
  transport_mode?: "driving" | "transit" | "walking" | "bicycling" | null
  tags?: string[]
  booking_required?: boolean
  fact_status?: "verified" | "reference" | "unverified" | null
  fact_refs?: string[]
  action?: {
    type: "map" | "booking" | "details" | "alternative" | "complete"
    label: string
  } | null
  map_role?: "hotel" | "attraction" | null
  map_group?: string | null
  map_label?: string | null
}

/* ---------------- 业务状态码（前端展示用，对齐 0.6） ---------------- */

export type BusinessCode = 0 | 1001 | 1002 | 1003 | 1004

/* ---------------- 前端页面目的地（映射到 Next.js App Router URL） ---------------- */

export type PageType =
  | "home"
  | "postcards"
  | "postcard-collection"
  | "reports"
  | "report-detail"
  | "planning"
  | "history"
  | "history-detail"
