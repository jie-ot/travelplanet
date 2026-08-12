import { Capacitor, registerPlugin } from "@capacitor/core"

interface PostcardSaverPlugin {
  saveImage(options: { url?: string; data?: string; fileName: string }): Promise<{ uri: string }>
}

const PostcardSaver = registerPlugin<PostcardSaverPlugin>("PostcardSaver")

export async function savePostcardImage(imageUrl: string, title: string): Promise<void> {
  return saveTravelImage(imageUrl, title, "旅行明信片")
}

export async function saveTravelImage(
  imageUrl: string,
  title: string,
  fallbackName = "旅行图片",
): Promise<void> {
  const fileName = `旅行星球-${sanitizeFileName(title, fallbackName)}`

  if (Capacitor.isNativePlatform()) {
    if (imageUrl.startsWith("blob:") || imageUrl.startsWith("data:")) {
      const data = imageUrl.startsWith("data:") ? imageUrl : await blobUrlToDataUrl(imageUrl)
      await PostcardSaver.saveImage({ data, fileName })
      return
    }

    const url = new URL(imageUrl, window.location.href).href
    await PostcardSaver.saveImage({ url, fileName })
    return
  }

  await downloadInBrowser(imageUrl, fileName)
}

export async function saveTravelImageBlob(
  blob: Blob,
  title: string,
  fallbackName = "旅行图片",
): Promise<void> {
  const objectUrl = URL.createObjectURL(blob)
  try {
    await saveTravelImage(objectUrl, title, fallbackName)
  } finally {
    URL.revokeObjectURL(objectUrl)
  }
}

async function downloadInBrowser(imageUrl: string, fileName: string): Promise<void> {
  const response = await fetch(imageUrl)
  if (!response.ok) throw new Error("图片下载失败")

  const blob = await response.blob()
  const objectUrl = URL.createObjectURL(blob)
  const extension = extensionForMimeType(blob.type)
  const anchor = document.createElement("a")
  anchor.href = objectUrl
  anchor.download = `${fileName}.${extension}`
  anchor.click()
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1_000)
}

async function blobUrlToDataUrl(blobUrl: string): Promise<string> {
  const response = await fetch(blobUrl)
  if (!response.ok) throw new Error("图片读取失败")
  const blob = await response.blob()

  return await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error ?? new Error("图片读取失败"))
    reader.readAsDataURL(blob)
  })
}

function sanitizeFileName(value: string, fallbackName: string): string {
  const normalized = value.trim().replace(/[\\/:*?"<>|]/g, "-").replace(/\s+/g, " ")
  return (normalized || fallbackName).slice(0, 80)
}

function extensionForMimeType(mimeType: string): string {
  if (mimeType.includes("png")) return "png"
  if (mimeType.includes("webp")) return "webp"
  if (mimeType.includes("gif")) return "gif"
  if (mimeType.includes("heic") || mimeType.includes("heif")) return "heic"
  return "jpg"
}
