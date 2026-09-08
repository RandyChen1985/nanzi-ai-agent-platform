import QRCode from 'qrcode'

/**
 * 将文本 (如 otpauth://totp/...) 转换为 Base64 DataURL 图片
 */
export async function generateQRCodeDataUrl(text: string): Promise<string> {
  if (!text) return ''
  try {
    return await QRCode.toDataURL(text, {
      width: 220,
      margin: 2,
      color: {
        dark: '#1e293b',
        light: '#ffffff',
      },
      errorCorrectionLevel: 'M',
    })
  } catch (err) {
    console.error('Failed to generate QR code:', err)
    return ''
  }
}
