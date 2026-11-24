// Operate only on accessibility elements belonging to the supplied test PID.
#import <AppKit/AppKit.h>
#import <ApplicationServices/ApplicationServices.h>

static id attribute(AXUIElementRef element, CFStringRef name) {
    CFTypeRef value = NULL;
    AXUIElementCopyAttributeValue(element, name, &value);
    return CFBridgingRelease(value);
}

static id cancelButton(AXUIElementRef element, int depth) {
    if ([attribute(element, kAXRoleAttribute) isEqual:@"AXButton"] &&
        [attribute(element, kAXTitleAttribute) isEqual:@"Cancel"]) {
        return (__bridge id)element;
    }
    if (depth > 0) {
        for (id child in attribute(element, kAXChildrenAttribute)) {
            id button = cancelButton((__bridge AXUIElementRef)child, depth - 1);
            if (button) return button;
        }
    }
    return nil;
}

int main(int argc, char **argv) {
    @autoreleasepool {
        if (argc != 3 || !AXIsProcessTrusted()) {
            fprintf(stderr, "Window test requires macOS Accessibility permission\n");
            return 1;
        }
        char *end = NULL;
        long pid = strtol(argv[1], &end, 10);
        BOOL close = strcmp(argv[2], "close") == 0;
        if (*end || pid <= 1 || pid > INT_MAX || (!close && strcmp(argv[2], "cancel"))) return 2;
        AXUIElementRef app = AXUIElementCreateApplication((pid_t)pid);
        NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:10];
        while ([deadline timeIntervalSinceNow] > 0) {
            for (id item in attribute(app, kAXWindowsAttribute)) {
                AXUIElementRef window = (__bridge AXUIElementRef)item;
                id button = close ? attribute(window, kAXCloseButtonAttribute) : cancelButton(window, 5);
                if (button) {
                    AXError result = AXUIElementPerformAction((__bridge AXUIElementRef)button, kAXPressAction);
                    CFRelease(app);
                    if (result != kAXErrorSuccess) fprintf(stderr, "AXPress failed: %d\n", result);
                    return result != kAXErrorSuccess;
                }
            }
            [NSThread sleepForTimeInterval:0.1];
        }
        fprintf(stderr, "Timed out finding %s button for PID %ld\n", argv[2], pid);
        CFRelease(app);
        return 1;
    }
}
