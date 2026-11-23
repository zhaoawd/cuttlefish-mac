/* Send a quit event only to the PID of the test's diskless QEMU. */
#include <Carbon/Carbon.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    if (argc != 2) {
        return 1;
    }
    char *end;
    long value = strtol(argv[1], &end, 10);
    if (*end || value <= 1 || value > INT32_MAX) {
        return 1;
    }
    pid_t target = (pid_t)value;
    AEAddressDesc address = {typeNull, NULL};
    AppleEvent event = {typeNull, NULL};
    OSStatus status = AECreateDesc(typeKernelProcessID, &target, sizeof(target), &address);
    if (status == noErr) {
        status = AECreateAppleEvent(kCoreEventClass, kAEQuitApplication, &address,
                                    kAutoGenerateReturnID, kAnyTransactionID, &event);
    }
    if (status == noErr) {
        status = AESendMessage(&event, NULL, kAENoReply | kAENeverInteract, kAEDefaultTimeout);
    }
    AEDisposeDesc(&event);
    AEDisposeDesc(&address);
    if (status != noErr) {
        fprintf(stderr, "AESendMessage: %d\n", (int)status);
        return 1;
    }
    printf("%d\n", getpid());
    return 0;
}
