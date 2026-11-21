package dev.cuttlefish.cfctl.fixture;

import android.bluetooth.*;
import android.content.Context;
import android.os.Bundle;
import java.util.Arrays;
import java.util.UUID;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;

final class BluetoothProbe extends BluetoothGattCallback {
    private final LinkedBlockingQueue<String> events = new LinkedBlockingQueue<>();
    private byte[] reply;
    private void expect(String stage) throws Exception {
        String event = events.poll(15, TimeUnit.SECONDS);
        if (!stage.equals(event)) throw new IllegalStateException("expected " + stage + ", received " + event);
    }
    @Override public void onConnectionStateChange(BluetoothGatt gatt, int status, int state) {
        events.add("connection " + status + " " + state);
    }
    @Override public void onServicesDiscovered(BluetoothGatt gatt, int status) { events.add("services " + status); }
    @Override public void onCharacteristicWrite(BluetoothGatt gatt, BluetoothGattCharacteristic c, int status) {
        events.add("write " + status);
    }
    @Override public void onCharacteristicRead(BluetoothGatt gatt, BluetoothGattCharacteristic c, byte[] value, int status) {
        reply = value;
        events.add("read " + status);
    }
    static String exchange(Context context, Bundle args) throws Exception {
        BluetoothAdapter adapter = context.getSystemService(BluetoothManager.class).getAdapter();
        BluetoothDevice peer = null;
        for (BluetoothDevice bonded : adapter.getBondedDevices()) {
            if (args.getString("name").equals(bonded.getName())) {
                if (peer != null) throw new IllegalStateException("duplicate bonded name");
                peer = bonded;
            }
        }
        if (peer == null) throw new IllegalStateException("test peer not found");
        if (peer.getBondState() != BluetoothDevice.BOND_BONDED) throw new IllegalStateException("peer is not bonded");
        BluetoothProbe callback = new BluetoothProbe();
        BluetoothGatt gatt = peer.connectGatt(context, false, callback, BluetoothDevice.TRANSPORT_LE);
        try {
            callback.expect("connection 0 2");
            if (!gatt.discoverServices()) throw new IllegalStateException("service discovery rejected");
            callback.expect("services 0");
            BluetoothGattService service = gatt.getService(UUID.fromString("31ec2624-6bd9-48e4-af07-69570abe8c80"));
            BluetoothGattCharacteristic echo = service.getCharacteristic(UUID.fromString("31ec2624-6bd9-48e4-af07-69570abe8c81"));
            byte[] data = new byte[16];
            new java.security.SecureRandom().nextBytes(data);
            if (gatt.writeCharacteristic(echo, data, BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT) != 0)
                throw new IllegalStateException("write rejected");
            callback.expect("write 0");
            if (!gatt.readCharacteristic(echo)) throw new IllegalStateException("read rejected");
            callback.expect("read 0");
            if (!Arrays.equals(data, callback.reply)) throw new IllegalStateException("echo mismatch");
            return "bonded encrypted echo bytes=16";
        } finally {
            gatt.disconnect();
            gatt.close();
        }
    }
}
