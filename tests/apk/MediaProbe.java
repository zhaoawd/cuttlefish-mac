package dev.cuttlefish.cfctl.fixture;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Intent;
import android.graphics.ImageFormat;
import android.hardware.camera2.*;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioTrack;
import android.media.Image;
import android.media.ImageReader;
import android.opengl.*;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.SystemClock;
import android.util.Size;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.FloatBuffer;
import java.util.Collections;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;

public final class MediaProbe extends Instrumentation {
    private Bundle arguments;

    @Override public void onCreate(Bundle args) {
        super.onCreate(args);
        arguments = args;
        start();
    }

    @Override public void onStart() {
        Bundle result = new Bundle();
        try {
            String evidence;
            switch (arguments.getString("test")) {
                case "bluetooth-data": evidence = BluetoothProbe.exchange(getTargetContext(), arguments); break;
                case "wifi-https":
                case "cellular-data":
                case "wifi-data": evidence = NetworkProbe.exchange(getTargetContext(), arguments); break;
                case "gles": evidence = graphics(); break;
                case "audio": evidence = audio(); break;
                case "camera": evidence = camera(); break;
                default: throw new IllegalArgumentException("unknown test");
            }
            result.putString("evidence", evidence);
            finish(Activity.RESULT_OK, result);
        } catch (Exception ex) {
            result.putString("error", ex.toString());
            finish(Activity.RESULT_CANCELED, result);
        }
    }

    private static void check(boolean condition, String message) {
        if (!condition) throw new IllegalStateException(message);
    }

    private static String graphics() {
        EGLDisplay display = EGL14.eglGetDisplay(EGL14.EGL_DEFAULT_DISPLAY);
        int[] value = new int[1];
        check(EGL14.eglInitialize(display, value, 0, value, 0), "eglInitialize");
        try {
            EGLConfig[] configs = new EGLConfig[1];
            int[] settings = {EGL14.EGL_SURFACE_TYPE, EGL14.EGL_PBUFFER_BIT, EGL14.EGL_RENDERABLE_TYPE,
                    EGL14.EGL_OPENGL_ES_BIT, EGL14.EGL_RED_SIZE, 8, EGL14.EGL_GREEN_SIZE, 8,
                    EGL14.EGL_BLUE_SIZE, 8, EGL14.EGL_DEPTH_SIZE, 16, EGL14.EGL_NONE};
            check(EGL14.eglChooseConfig(display, settings, 0, configs, 0, 1, value, 0)
                    && value[0] == 1, "eglChooseConfig");
            EGLContext ctx = EGL14.eglCreateContext(display, configs[0], EGL14.EGL_NO_CONTEXT,
                    new int[]{EGL14.EGL_CONTEXT_CLIENT_VERSION, 1, EGL14.EGL_NONE}, 0);
            EGLSurface surface = EGL14.eglCreatePbufferSurface(display, configs[0],
                    new int[]{EGL14.EGL_WIDTH, 32, EGL14.EGL_HEIGHT, 32, EGL14.EGL_NONE}, 0);
            try {
                check(EGL14.eglMakeCurrent(display, surface, surface, ctx), "eglMakeCurrent");
                GLES10.glViewport(0, 0, 32, 32);
                GLES10.glClearColor(0, 0, 1, 1);
                GLES10.glClear(GLES10.GL_COLOR_BUFFER_BIT | GLES10.GL_DEPTH_BUFFER_BIT);
                GLES10.glEnable(GLES10.GL_DEPTH_TEST);
                GLES10.glEnableClientState(GLES10.GL_VERTEX_ARRAY);
                // Draw the near green triangle first, then the far red triangle.
                float[] coords = {-1,-1,-0.5f, 1,-1,-0.5f, 0,1,-0.5f, -1,-1,0.5f, 1,-1,0.5f, 0,1,0.5f};
                FloatBuffer vertices = ByteBuffer.allocateDirect(coords.length * 4)
                        .order(ByteOrder.nativeOrder()).asFloatBuffer();
                vertices.put(coords).position(0);
                GLES10.glVertexPointer(3, GLES10.GL_FLOAT, 0, vertices);
                GLES10.glColor4f(0, 1, 0, 1);
                GLES10.glDrawArrays(GLES10.GL_TRIANGLES, 0, 3);
                GLES10.glColor4f(1, 0, 0, 1);
                GLES10.glDrawArrays(GLES10.GL_TRIANGLES, 3, 3);
                ByteBuffer pixel = ByteBuffer.allocateDirect(4);
                GLES10.glReadPixels(16, 16, 1, 1, GLES10.GL_RGBA, GLES10.GL_UNSIGNED_BYTE, pixel);
                check(GLES10.glGetError() == GLES10.GL_NO_ERROR && pixel.get(0) == 0
                        && (pixel.get(1) & 255) == 255 && pixel.get(2) == 0, "depth-tested pixel mismatch");
                return "center=0,255,0; " + GLES10.glGetString(GLES10.GL_RENDERER);
            } finally {
                EGL14.eglMakeCurrent(display, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_CONTEXT);
                EGL14.eglDestroySurface(display, surface);
                EGL14.eglDestroyContext(display, ctx);
            }
        } finally {
            EGL14.eglTerminate(display);
        }
    }

    private static String audio() {
        short[] pcm = new short[16000];
        AudioTrack track = new AudioTrack(AudioManager.STREAM_MUSIC, 16000,
                AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT, pcm.length * 2, AudioTrack.MODE_STATIC);
        try {
            check(track.write(pcm, 0, pcm.length) == pcm.length, "PCM write failed");
            track.play();
            long end = SystemClock.elapsedRealtime() + 5000;
            int played;
            do {
                SystemClock.sleep(50);
                played = track.getPlaybackHeadPosition();
            } while (played < pcm.length && SystemClock.elapsedRealtime() < end);
            check(played == pcm.length, "playback head=" + played);
            return "played=" + played + " frames";
        } finally {
            track.release();
        }
    }

    private String camera() throws Exception {
        Intent intent = new Intent(getTargetContext(), Main.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        Activity activity = startActivitySync(intent);
        HandlerThread thread = new HandlerThread("camera-probe");
        thread.start();
        Handler handler = new Handler(thread.getLooper());
        try {
            CameraManager manager = activity.getSystemService(CameraManager.class);
            String[] ids = manager.getCameraIdList();
            check(ids.length > 0, "no camera");
            String id = ids[0];
            Size[] sizes = manager.getCameraCharacteristics(id).get(CameraCharacteristics
                    .SCALER_STREAM_CONFIGURATION_MAP).getOutputSizes(ImageFormat.YUV_420_888);
            Size size = sizes[0];
            for (Size option : sizes) {
                if ((long)option.getWidth() * option.getHeight() < (long)size.getWidth() * size.getHeight()) size = option;
            }
            ArrayBlockingQueue<CameraDevice> opened = new ArrayBlockingQueue<>(1);
            manager.openCamera(id, new CameraDevice.StateCallback() {
                public void onOpened(CameraDevice device) { opened.offer(device); }
                public void onDisconnected(CameraDevice device) { device.close(); }
                public void onError(CameraDevice device, int error) { device.close(); }
            }, handler);
            try (CameraDevice device = opened.poll(8, TimeUnit.SECONDS);
                 ImageReader reader = ImageReader.newInstance(size.getWidth(), size.getHeight(), ImageFormat.YUV_420_888, 2)) {
                check(device != null, "camera open timeout");
                ArrayBlockingQueue<CameraCaptureSession> ready = new ArrayBlockingQueue<>(1);
                device.createCaptureSession(Collections.singletonList(reader.getSurface()), new CameraCaptureSession.StateCallback() {
                    public void onConfigured(CameraCaptureSession session) { ready.offer(session); }
                    public void onConfigureFailed(CameraCaptureSession session) { session.close(); }
                }, handler);
                try (CameraCaptureSession session = ready.poll(8, TimeUnit.SECONDS)) {
                    check(session != null, "camera configuration timeout");
                    ArrayBlockingQueue<Integer> frames = new ArrayBlockingQueue<>(1);
                    reader.setOnImageAvailableListener(source -> {
                        try (Image image = source.acquireLatestImage()) {
                            if (image != null) frames.offer(image.getPlanes()[0].getBuffer().remaining());
                        }
                    }, handler);
                    CaptureRequest.Builder request = device.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW);
                    request.addTarget(reader.getSurface());
                    session.capture(request.build(), null, handler);
                    Integer bytes = frames.poll(8, TimeUnit.SECONDS);
                    check(bytes != null && bytes >= size.getWidth() * size.getHeight(), "YUV frame unavailable");
                    return "id=" + id + " size=" + size + " Y bytes=" + bytes;
                }
            }
        } finally {
            thread.quitSafely();
            runOnMainSync(activity::finish);
        }
    }
}
