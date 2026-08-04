/**
 * 业务 API 门面（唯一对外入口）。
 * View 与 AppProvider 只依赖本文件，所有请求由 real-api 和 http-client 统一处理。
 */
import type {
  ItineraryData,
  Plan,
  PlanningBrief,
  PlanningChatMessage,
  PlanningModel,
  PlanningResponse,
  PostcardGroup,
  Report,
  UploadedPhoto,
} from "@/types"
import { realApi } from "./real-api"

export interface GenerateInput {
  photos: UploadedPhoto[]
  requirements: string
  options: { generatePostcards: boolean; generateReport: boolean }
}

export interface GenerateResult {
  postcardGroup: PostcardGroup | null
  report: Report | null
}

export interface PlanWithAIInput {
  message: string
  planningModel: PlanningModel
  context: ItineraryData | null
  messages?: PlanningChatMessage[]
  brief?: PlanningBrief | null
  confirmed?: boolean
}

/** 后端能力契约。 */
export interface TravelApi {
  // 初始化全量加载
  getPostcardGroups(): Promise<PostcardGroup[]>
  getReports(): Promise<Report[]>
  getPlans(): Promise<Plan[]>

  // 旅行后流
  uploadImage(file: File): Promise<{ assetId: string; imageUrl: string }>
  generateTravelArtifacts(input: GenerateInput): Promise<GenerateResult>

  // 旅行前流
  planWithAI(input: PlanWithAIInput): Promise<PlanningResponse>
  createPlan(input: { itineraryData: ItineraryData }): Promise<Plan>
  updatePlan(id: string, input: { itineraryData: ItineraryData }): Promise<Plan>

  // 删除
  deletePostcardGroup(id: string): Promise<void>
  deleteReport(id: string): Promise<void>
  deletePlan(id: string): Promise<void>
}

export function getPostcardGroups(): Promise<PostcardGroup[]> {
  return realApi.getPostcardGroups()
}
export function getReports(): Promise<Report[]> {
  return realApi.getReports()
}
export function getPlans(): Promise<Plan[]> {
  return realApi.getPlans()
}
export function uploadImage(file: File): Promise<{ assetId: string; imageUrl: string }> {
  return realApi.uploadImage(file)
}
export function generateTravelArtifacts(input: GenerateInput): Promise<GenerateResult> {
  return realApi.generateTravelArtifacts(input)
}
export function planWithAI(input: PlanWithAIInput): Promise<PlanningResponse> {
  return realApi.planWithAI(input)
}
export function createPlan(input: { itineraryData: ItineraryData }): Promise<Plan> {
  return realApi.createPlan(input)
}
export function updatePlan(id: string, input: { itineraryData: ItineraryData }): Promise<Plan> {
  return realApi.updatePlan(id, input)
}
export function deletePostcardGroup(id: string): Promise<void> {
  return realApi.deletePostcardGroup(id)
}
export function deleteReport(id: string): Promise<void> {
  return realApi.deleteReport(id)
}
export function deletePlan(id: string): Promise<void> {
  return realApi.deletePlan(id)
}
