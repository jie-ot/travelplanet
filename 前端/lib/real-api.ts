/**
 * 真实后端适配实现。
 *
 * 所有请求经 lib/http-client.ts 的 apiClient：拼 base、解包 { code, message, data }、抛 AppError。
 * 端点路径不含 /api 前缀（base 已含），严格对齐《数据结构与通信接口规范》三与四。
 * 本文件不处理 code/message/data，也不做本地数据兜底。
 */
import type {
  ItineraryData,
  Plan,
  PlanningProgressSnapshot,
  PlanningResponse,
  PostcardGroup,
  Report,
} from "@/types"
import { apiClient, jsonInit } from "./http-client"
import type { GenerateInput, GenerateResult, PlanWithAIInput, TravelApi } from "./api"

export const realApi: TravelApi = {
  getPostcardGroups() {
    return apiClient<PostcardGroup[]>("/postcard-groups")
  },

  getReports() {
    return apiClient<Report[]>("/reports")
  },

  getPlans() {
    return apiClient<Plan[]>("/plans")
  },

  uploadImage(file: File) {
    const form = new FormData()
    form.append("file", file)
    // multipart/form-data：严禁手动设置 Content-Type，交由浏览器生成带 boundary 的请求头
    return apiClient<{ assetId: string; imageUrl: string }>("/images/upload", {
      method: "POST",
      body: form,
    })
  },

  generateTravelArtifacts(input: GenerateInput) {
    return apiClient<GenerateResult>("/generate", jsonInit("POST", input))
  },

  planWithAI(input: PlanWithAIInput) {
    return apiClient<PlanningResponse>("/ai/planning", jsonInit("POST", input))
  },

  getPlanningProgress(token: string) {
    return apiClient<PlanningProgressSnapshot | null>(
      `/ai/planning/progress?token=${encodeURIComponent(token)}`,
    )
  },

  createPlan(input: { itineraryData: ItineraryData }) {
    return apiClient<Plan>("/plans", jsonInit("POST", input))
  },

  updatePlan(id: string, input: { itineraryData: ItineraryData }) {
    return apiClient<Plan>(`/plans/${id}`, jsonInit("PUT", input))
  },

  async deletePostcardGroup(id: string) {
    await apiClient<null>(`/postcard-groups/${id}`, jsonInit("DELETE"))
  },

  async deleteReport(id: string) {
    await apiClient<null>(`/reports/${id}`, jsonInit("DELETE"))
  },

  async deletePlan(id: string) {
    await apiClient<null>(`/plans/${id}`, jsonInit("DELETE"))
  },
}
