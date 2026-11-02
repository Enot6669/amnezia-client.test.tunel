#!/usr/bin/env python3
"""Apply the Amnezia reverse-mode changes to the pinned Mullvad 1.3.0.0 source tree.

The script intentionally uses exact, single-occurrence anchors. If upstream source
changes, it stops instead of silently producing a partially patched kernel driver.
"""
from pathlib import Path
import sys

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")

def edit(path, replacements):
    file_path = ROOT / path
    source = file_path.read_text(encoding="utf-8-sig")
    for old, new in replacements:
        count = source.count(old)
        if count != 1:
            raise SystemExit(f"{path}: expected one patch anchor, found {count}: {old[:100]!r}")
        source = source.replace(old, new, 1)
    file_path.write_text(source, encoding="utf-8", newline="\n")

edit("src/defs/ioctl.h", [(
"""#define IOCTL_ST_RESET \\
\tCTL_CODE(ST_DEVICE_TYPE, 11, METHOD_NEITHER, FILE_ANY_ACCESS)
""",
"""#define IOCTL_ST_RESET \\
\tCTL_CODE(ST_DEVICE_TYPE, 11, METHOD_NEITHER, FILE_ANY_ACCESS)

// Reverse split mode uses a separate IOCTL so the legacy configuration
// structure and legacy IOCTL remain binary-compatible.
#define IOCTL_ST_SET_CONFIGURATION_REVERSE \\
\tCTL_CODE(ST_DEVICE_TYPE, 12, METHOD_BUFFERED, FILE_ANY_ACCESS)

#define IOCTL_ST_GET_CAPABILITIES \\
\tCTL_CODE(ST_DEVICE_TYPE, 13, METHOD_BUFFERED, FILE_ANY_ACCESS)
""")])

edit("src/devicecontext.h", [(
"""\t// Protected by state lock.
\tREGISTERED_IMAGE_MGMT RegisteredImage;
""",
"""\t// Protected by state lock. True means configured apps use the VPN and
\t// all other processes are split out to the primary network interface.
\tbool ReverseMode;

\t// Protected by state lock.
\tREGISTERED_IMAGE_MGMT RegisteredImage;
""")])

edit("src/procmgmt/context.h", [(
"""\tENGAGED_STATE_ACTIVE_FN EngagedStateActive;

    void *CallbackContext;
""",
"""\tENGAGED_STATE_ACTIVE_FN EngagedStateActive;

    // Points to the device-context mode flag, protected by the state lock.
    bool *ReverseMode;

    void *CallbackContext;
""")])

edit("src/procmgmt/procmgmt.h", [(
"""\tENGAGED_STATE_ACTIVE_FN EngagedStateActive,
    void *CallbackContext
""",
"""\tENGAGED_STATE_ACTIVE_FN EngagedStateActive,
    bool *ReverseMode,
    void *CallbackContext
""")])

edit("src/procmgmt/procmgmt.cpp", [
(
"""    auto registeredImage = Context->RegisteredImage->Instance;

    if (registeredimage::HasEntryExact(registeredImage, &RegistryEntry->ImageName))
""",
"""    auto registeredImage = Context->RegisteredImage->Instance;

    // In reverse mode, exact allow-listed images stay in the VPN (Split OFF).
    // Every other image is split to the primary network. Parent state is
    // deliberately ignored: each process is classified by its own image path.
    if (*Context->ReverseMode)
    {
        if (!registeredimage::HasEntryExact(registeredImage, &RegistryEntry->ImageName))
        {
            RegistryEntry->Settings.Split = ST_PROCESS_SPLIT_STATUS_ON_BY_CONFIG;
            ArrivalEvent->SplittingReason |= ST_SPLITTING_REASON_BY_CONFIG;
            goto Duplicate_imagename;
        }

        return;
    }

    if (registeredimage::HasEntryExact(registeredImage, &RegistryEntry->ImageName))
"""
),
(
"""\tENGAGED_STATE_ACTIVE_FN EngagedStateActive,
    void *CallbackContext
)
""",
"""\tENGAGED_STATE_ACTIVE_FN EngagedStateActive,
    bool *ReverseMode,
    void *CallbackContext
)
"""),
(
"""    context->EngagedStateActive = EngagedStateActive;
    context->CallbackContext = CallbackContext;
""",
"""    context->EngagedStateActive = EngagedStateActive;
    context->ReverseMode = ReverseMode;
    context->CallbackContext = CallbackContext;
""")
])

edit("src/ioctl.h", [(
"""NTSTATUS
SetConfiguration
(
    WDFDEVICE Device,
    registeredimage::CONTEXT *Imageset
);
""",
"""NTSTATUS
SetConfiguration
(
    WDFDEVICE Device,
    registeredimage::CONTEXT *Imageset,
    bool ReverseMode
);
""")])

edit("src/ioctl.cpp", [
(
"""    if (registeredimage::HasEntryExact(context->RegisteredImage.Instance, &Entry->ImageName))
    {
        Entry->TargetSettings.Split = ST_PROCESS_SPLIT_STATUS_ON_BY_CONFIG;
    }
    else if (Entry->ParentProcessId == 0
""",
"""    if (context->ReverseMode)
    {
        Entry->TargetSettings.Split =
            registeredimage::HasEntryExact(context->RegisteredImage.Instance, &Entry->ImageName)
                ? ST_PROCESS_SPLIT_STATUS_OFF
                : ST_PROCESS_SPLIT_STATUS_ON_BY_CONFIG;
        return true;
    }

    if (registeredimage::HasEntryExact(context->RegisteredImage.Instance, &Entry->ImageName))
    {
        Entry->TargetSettings.Split = ST_PROCESS_SPLIT_STATUS_ON_BY_CONFIG;
    }
    else if (Entry->ParentProcessId == 0
"""),
(
"""    if (!util::SplittingEnabled(Entry->TargetSettings.Split))
    {
        auto currentEntry = Entry;
""",
"""    if (!context->ReverseMode && !util::SplittingEnabled(Entry->TargetSettings.Split))
    {
        auto currentEntry = Entry;
"""),
(
"""        CallbackEngagedStateActive,
        context
""",
"""        CallbackEngagedStateActive,
        &context->ReverseMode,
        context
"""),
(
"""SetConfiguration
(
    WDFDEVICE Device,
    registeredimage::CONTEXT *Imageset
)
{
    auto context = DeviceGetSplitTunnelContext(Device);

    NTSTATUS status = STATUS_UNSUCCESSFUL;

    WdfWaitLockAcquire(context->DriverState.Lock, NULL);
""",
"""SetConfiguration
(
    WDFDEVICE Device,
    registeredimage::CONTEXT *Imageset,
    bool ReverseMode
)
{
    auto context = DeviceGetSplitTunnelContext(Device);

    NTSTATUS status = STATUS_UNSUCCESSFUL;

    WdfWaitLockAcquire(context->DriverState.Lock, NULL);

    const bool previousReverseMode = context->ReverseMode;
    context->ReverseMode = ReverseMode;
"""),
(
"""    WdfWaitLockRelease(context->DriverState.Lock);

    if (NT_SUCCESS(status))
    {
        DbgPrint("Successfully processed IOCTL_ST_SET_CONFIGURATION\\n");
""",
"""    if (!NT_SUCCESS(status))
    {
        // Keep the previous policy if the WFP transaction or state transition failed.
        context->ReverseMode = previousReverseMode;
    }

    WdfWaitLockRelease(context->DriverState.Lock);

    if (NT_SUCCESS(status))
    {
        DbgPrint("Successfully processed configuration update (reverse=%u)\\n", ReverseMode ? 1 : 0);
"""),
(
"""    registeredimage::Reset(context->RegisteredImage.Instance);

    WdfWaitLockRelease(context->DriverState.Lock);
""",
"""    registeredimage::Reset(context->RegisteredImage.Instance);
    context->ReverseMode = false;

    WdfWaitLockRelease(context->DriverState.Lock);
""")
])

edit("src/driverentry.cpp", [(
"""            // IOCTL_ST_SET_CONFIGURATION
            // IOCTL_ST_GET_CONFIGURATION
""",
"""            // IOCTL_ST_SET_CONFIGURATION
            // IOCTL_ST_SET_CONFIGURATION_REVERSE
            // IOCTL_ST_GET_CONFIGURATION
"""),
(
"""            if (IoControlCode == IOCTL_ST_SET_CONFIGURATION)
            {
                registeredimage::CONTEXT *imageset;

                auto status = ioctl::SetConfigurationPrepare(Request, &imageset);
""",
"""            if (IoControlCode == IOCTL_ST_SET_CONFIGURATION
                || IoControlCode == IOCTL_ST_SET_CONFIGURATION_REVERSE)
            {
                const bool reverseMode = IoControlCode == IOCTL_ST_SET_CONFIGURATION_REVERSE;
                registeredimage::CONTEXT *imageset;

                auto status = ioctl::SetConfigurationPrepare(Request, &imageset);
"""),
(
"""                status = ioctl::SetConfiguration(device, imageset);
""",
"""                status = ioctl::SetConfiguration(device, imageset, reverseMode);
""")
])


edit("src/validation.cpp", [(
"""    const auto status = RtlULongPtrAdd(
        (ULONG_PTR)((UCHAR*)Buffer + sizeof(ST_CONFIGURATION_HEADER)),
        entriesSize,
        (ULONG_PTR*)&stringBuffer
    );

    if (STATUS_SUCCESS != status || stringBuffer >= bufferEnd)
    {
        return false;
    }
""",
"""    const auto status = RtlULongPtrAdd(
        (ULONG_PTR)((UCHAR*)Buffer + sizeof(ST_CONFIGURATION_HEADER)),
        entriesSize,
        (ULONG_PTR*)&stringBuffer
    );

    if (STATUS_SUCCESS != status || stringBuffer > bufferEnd)
    {
        return false;
    }

    // An empty allow-list is valid only for reverse mode. The reverse-mode
    // IOCTL accepts a header-only buffer; legacy mode still rejects it below.
    if (header->NumEntries == 0)
    {
        return stringBuffer == bufferEnd;
    }

    if (stringBuffer == bufferEnd)
    {
        return false;
    }
""")])

edit("src/ioctl.h", [(
"""SetConfigurationPrepare
(
    WDFREQUEST Request,
    registeredimage::CONTEXT **Imageset
);
""",
"""SetConfigurationPrepare
(
    WDFREQUEST Request,
    registeredimage::CONTEXT **Imageset,
    bool ReverseMode
);
""")])

edit("src/ioctl.cpp", [
(
"""SetConfigurationPrepare
(
    WDFREQUEST Request,
    registeredimage::CONTEXT **Imageset
)
""",
"""SetConfigurationPrepare
(
    WDFREQUEST Request,
    registeredimage::CONTEXT **Imageset,
    bool ReverseMode
)
"""),
(
"""    if (header->NumEntries == 0)
    {
        DbgPrint("Cannot assign empty configuration""",
"""    if (header->NumEntries == 0 && !ReverseMode)
    {
        DbgPrint("Cannot assign empty legacy configuration"""),
(
"""    if (registeredimage::IsEmpty(Context->RegisteredImage.Instance))
    {
        Context->IpAddresses = *newIpAddresses;
""",
"""    if (registeredimage::IsEmpty(Context->RegisteredImage.Instance) && !Context->ReverseMode)
    {
        Context->IpAddresses = *newIpAddresses;
"""),

])

edit("src/driverentry.cpp", [(
"""auto status = ioctl::SetConfigurationPrepare(Request, &imageset);""",
"""auto status = ioctl::SetConfigurationPrepare(Request, &imageset, reverseMode);"""
)])

edit("src/ioctl.cpp", [(
"""    Context->DriverState.State = ST_DRIVER_STATE_STARTED;

    WdfWaitLockRelease(Context->DriverState.Lock);
""",
"""    Context->DriverState.State = ST_DRIVER_STATE_STARTED;
    Context->ReverseMode = false;

    WdfWaitLockRelease(Context->DriverState.Lock);
""")])



edit("src/procmgmt/procmgmt.cpp", [(
"""    if (*Context->ReverseMode)
    {
        if (!registeredimage::HasEntryExact""",
"""    if (*Context->ReverseMode)
    {
        // Some kernel/system processes have no image path and cannot be
        // represented by an ALE_APP_ID WFP filter. Leave those unsplit.
        if (RegistryEntry->ImageName.Length == 0)
        {
            return;
        }

        if (!registeredimage::HasEntryExact""")])

edit("src/ioctl.cpp", [(
"""    if (context->ReverseMode)
    {
        Entry->TargetSettings.Split =
""",
"""    if (context->ReverseMode)
    {
        // Do not create an ALE_APP_ID filter for a process with no image path.
        if (Entry->ImageName.Length == 0)
        {
            Entry->TargetSettings.Split = ST_PROCESS_SPLIT_STATUS_OFF;
            return true;
        }

        Entry->TargetSettings.Split =
""")])

edit("src/ioctl.cpp", [(
"""    RtlZeroMemory(&Context->IpAddresses, sizeof(Context->IpAddresses));""",
"""    RtlZeroMemory(&Context->IpAddresses, sizeof(Context->IpAddresses));
    Context->ReverseMode = false;"""
)])

edit("src/driverentry.cpp", [(
"""    if (IoControlCode == IOCTL_ST_GET_STATE)
    {
        ioctl::GetStateComplete(device, Request);

        return;
    }
""",
"""    if (IoControlCode == IOCTL_ST_GET_STATE)
    {
        ioctl::GetStateComplete(device, Request);

        return;
    }

    // Capability query is available in every driver state. Older signed
    // upstream binaries do not recognize this IOCTL and therefore fail closed.
    if (IoControlCode == IOCTL_ST_GET_CAPABILITIES)
    {
        PVOID outputBuffer;
        size_t outputBufferLength;
        auto status = WdfRequestRetrieveOutputBuffer(
            Request, sizeof(ULONG), &outputBuffer, &outputBufferLength);
        if (!NT_SUCCESS(status))
        {
            WdfRequestComplete(Request, status);
            return;
        }

        *(ULONG*)outputBuffer = 0x00000001; // Reverse split mode supported.
        WdfRequestCompleteWithInformation(Request, STATUS_SUCCESS, sizeof(ULONG));
        return;
    }
""")])

print("Reverse-mode driver patch applied successfully.")
