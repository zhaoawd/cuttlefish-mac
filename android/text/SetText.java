package dev.cuttlefish.cfctl.text;

import android.accessibilityservice.AccessibilityServiceInfo;
import android.app.Activity;
import android.app.Instrumentation;
import android.app.UiAutomation;
import android.os.Bundle;
import android.os.SystemClock;
import android.util.Base64;
import android.view.accessibility.AccessibilityNodeInfo;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;

public class SetText extends Instrumentation {
    private Bundle args;

    @Override public void onCreate(Bundle arguments) {
        super.onCreate(arguments);
        args = arguments;
        start();
    }

    private AccessibilityNodeInfo focusedField(UiAutomation ui) {
        AccessibilityNodeInfo root = ui.getRootInActiveWindow();
        long until = SystemClock.uptimeMillis() + 4000;
        while (root == null && SystemClock.uptimeMillis() < until) {
            SystemClock.sleep(100);
            root = ui.getRootInActiveWindow();
        }
        if (root == null) throw new IllegalStateException("active window unavailable");
        ArrayDeque<AccessibilityNodeInfo> nodes = new ArrayDeque<>();
        nodes.add(root);
        AccessibilityNodeInfo match = null;
        while (!nodes.isEmpty()) {
            AccessibilityNodeInfo node = nodes.removeFirst();
            if (node.isFocused() && node.isVisibleToUser()) {
                if (match != null) throw new IllegalStateException("multiple focused nodes");
                match = node;
            }
            for (int i = 0; i < node.getChildCount(); i++) {
                AccessibilityNodeInfo child = node.getChild(i);
                if (child != null) nodes.addLast(child);
            }
        }
        return match;
    }

    @Override public void onStart() {
        Bundle result = new Bundle();
        try {
            UiAutomation ui = getUiAutomation(UiAutomation.FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES);
            AccessibilityServiceInfo info = ui.getServiceInfo();
            info.flags |= AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS;
            ui.setServiceInfo(info);
            AccessibilityNodeInfo node = focusedField(ui);
            if (node == null || !node.isEditable() || node.isPassword() || !node.isEnabled()
                    || !"android.widget.EditText".contentEquals(node.getClassName())
                    || !args.getString("pkg").equals(String.valueOf(node.getPackageName()))) {
                throw new IllegalStateException("no matching focused editable field");
            }
            String key = args.getString("key");
            String selector = args.getString("selector");
            if (("resource-id".equals(key) && !selector.equals(node.getViewIdResourceName()))
                    || ("content-desc".equals(key) && !selector.contentEquals(
                            node.getContentDescription() == null ? "" : node.getContentDescription()))) {
                throw new IllegalStateException("field selector changed");
            }
            String value = new String(Base64.decode(args.getString("value"),
                    Base64.DEFAULT), StandardCharsets.UTF_8);
            Bundle action = new Bundle();
            action.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value);
            if (!node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, action)) {
                throw new IllegalStateException("field rejected ACTION_SET_TEXT");
            }
            result.putString("cfctl", "ok");
            finish(Activity.RESULT_OK, result);
        } catch (RuntimeException ex) {
            result.putString("cfctl", ex.toString());
            finish(Activity.RESULT_CANCELED, result);
        }
    }
}
