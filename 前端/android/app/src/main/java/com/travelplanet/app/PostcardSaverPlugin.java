package com.travelplanet.app;

import android.Manifest;
import android.content.ContentResolver;
import android.content.ContentValues;
import android.media.MediaScannerConnection;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;
import android.util.Base64;
import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;
import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Locale;

@CapacitorPlugin(
    name = "PostcardSaver",
    permissions = {
        @Permission(alias = "storage", strings = { Manifest.permission.WRITE_EXTERNAL_STORAGE })
    }
)
public class PostcardSaverPlugin extends Plugin {
    @PluginMethod
    public void saveImage(PluginCall call) {
        if (Build.VERSION.SDK_INT <= Build.VERSION_CODES.P && getPermissionState("storage") != PermissionState.GRANTED) {
            requestPermissionForAlias("storage", call, "storagePermissionCallback");
            return;
        }
        saveImageOnBridgeThread(call);
    }

    @PermissionCallback
    private void storagePermissionCallback(PluginCall call) {
        if (getPermissionState("storage") != PermissionState.GRANTED) {
            call.reject("需要相册存储权限才能保存明信片");
            return;
        }
        saveImageOnBridgeThread(call);
    }

    private void saveImageOnBridgeThread(PluginCall call) {
        getBridge().execute(() -> {
            try {
                SavedImage savedImage = persistImage(call);
                JSObject result = new JSObject();
                result.put("uri", savedImage.uri);
                call.resolve(result);
            } catch (Exception error) {
                call.reject("保存明信片失败", error);
            }
        });
    }

    private SavedImage persistImage(PluginCall call) throws IOException {
        String requestedName = call.getString("fileName", "旅行明信片");
        String data = call.getString("data");
        String url = call.getString("url");

        if ((data == null || data.isEmpty()) && (url == null || url.isEmpty())) {
            throw new IOException("缺少图片地址");
        }

        ImageSource source = data != null && !data.isEmpty() ? fromDataUrl(data) : fromUrl(url);
        try (InputStream input = source.input) {
            String fileName = buildFileName(requestedName, source.mimeType);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                return saveWithMediaStore(input, fileName, source.mimeType);
            }
            return saveLegacy(input, fileName, source.mimeType);
        }
    }

    private ImageSource fromUrl(String urlValue) throws IOException {
        HttpURLConnection connection = (HttpURLConnection) new URL(urlValue).openConnection();
        connection.setConnectTimeout(15_000);
        connection.setReadTimeout(30_000);
        connection.setInstanceFollowRedirects(true);
        connection.setRequestProperty("Accept", "image/*");
        connection.connect();

        int status = connection.getResponseCode();
        if (status < 200 || status >= 300) {
            connection.disconnect();
            throw new IOException("图片下载失败: HTTP " + status);
        }

        String mimeType = normalizeMimeType(connection.getContentType());
        return new ImageSource(connection.getInputStream(), mimeType);
    }

    private ImageSource fromDataUrl(String dataUrl) throws IOException {
        int comma = dataUrl.indexOf(',');
        if (!dataUrl.startsWith("data:image/") || comma < 0) {
            throw new IOException("图片数据格式不正确");
        }

        String header = dataUrl.substring(5, comma);
        String mimeType = normalizeMimeType(header.split(";")[0]);
        byte[] bytes = Base64.decode(dataUrl.substring(comma + 1), Base64.DEFAULT);
        return new ImageSource(new ByteArrayInputStream(bytes), mimeType);
    }

    private SavedImage saveWithMediaStore(InputStream input, String fileName, String mimeType) throws IOException {
        ContentResolver resolver = getContext().getContentResolver();
        ContentValues values = new ContentValues();
        values.put(MediaStore.Images.Media.DISPLAY_NAME, fileName);
        values.put(MediaStore.Images.Media.MIME_TYPE, mimeType);
        values.put(MediaStore.Images.Media.RELATIVE_PATH, Environment.DIRECTORY_PICTURES + "/旅行星球");
        values.put(MediaStore.Images.Media.IS_PENDING, 1);

        Uri uri = resolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values);
        if (uri == null) throw new IOException("无法创建相册文件");

        try {
            try (OutputStream output = resolver.openOutputStream(uri)) {
                if (output == null) throw new IOException("无法写入相册文件");
                copy(input, output);
            }
            values.clear();
            values.put(MediaStore.Images.Media.IS_PENDING, 0);
            resolver.update(uri, values, null, null);
            return new SavedImage(uri.toString());
        } catch (Exception error) {
            resolver.delete(uri, null, null);
            if (error instanceof IOException) throw (IOException) error;
            throw new IOException("写入系统相册失败", error);
        }
    }

    @SuppressWarnings("deprecation")
    private SavedImage saveLegacy(InputStream input, String fileName, String mimeType) throws IOException {
        File directory = new File(
            Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_PICTURES),
            "旅行星球"
        );
        if (!directory.exists() && !directory.mkdirs()) throw new IOException("无法创建相册目录");

        File target = uniqueFile(directory, fileName);
        try (OutputStream output = new FileOutputStream(target)) {
            copy(input, output);
        }
        MediaScannerConnection.scanFile(
            getContext(),
            new String[] { target.getAbsolutePath() },
            new String[] { mimeType },
            null
        );
        return new SavedImage(Uri.fromFile(target).toString());
    }

    private File uniqueFile(File directory, String fileName) {
        File candidate = new File(directory, fileName);
        if (!candidate.exists()) return candidate;

        int dot = fileName.lastIndexOf('.');
        String stem = dot > 0 ? fileName.substring(0, dot) : fileName;
        String extension = dot > 0 ? fileName.substring(dot) : "";
        int suffix = 2;
        while (candidate.exists()) {
            candidate = new File(directory, stem + "-" + suffix + extension);
            suffix++;
        }
        return candidate;
    }

    private String buildFileName(String requestedName, String mimeType) {
        String safeName = requestedName
            .replaceAll("[\\\\/:*?\"<>|]", "-")
            .replaceAll("\\s+", " ")
            .trim();
        if (safeName.isEmpty()) safeName = "旅行明信片";
        if (safeName.length() > 80) safeName = safeName.substring(0, 80);
        return safeName + extensionForMimeType(mimeType);
    }

    private String normalizeMimeType(String mimeType) {
        if (mimeType == null) return "image/jpeg";
        String normalized = mimeType.split(";")[0].trim().toLowerCase(Locale.ROOT);
        return normalized.startsWith("image/") ? normalized : "image/jpeg";
    }

    private String extensionForMimeType(String mimeType) {
        if ("image/png".equals(mimeType)) return ".png";
        if ("image/webp".equals(mimeType)) return ".webp";
        if ("image/gif".equals(mimeType)) return ".gif";
        if ("image/heic".equals(mimeType) || "image/heif".equals(mimeType)) return ".heic";
        return ".jpg";
    }

    private void copy(InputStream input, OutputStream output) throws IOException {
        byte[] buffer = new byte[16 * 1024];
        int count;
        while ((count = input.read(buffer)) != -1) {
            output.write(buffer, 0, count);
        }
    }

    private static final class ImageSource {
        final InputStream input;
        final String mimeType;

        ImageSource(InputStream input, String mimeType) {
            this.input = input;
            this.mimeType = mimeType;
        }
    }

    private static final class SavedImage {
        final String uri;

        SavedImage(String uri) {
            this.uri = uri;
        }
    }
}
