package dev.cuttlefish.cfctl.fixture;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.LinkProperties;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.os.Bundle;
import java.io.DataInputStream;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;

final class WifiProbe {
    static String exchange(Context context, Bundle args) throws IOException {
        ConnectivityManager manager = context.getSystemService(ConnectivityManager.class);
        byte[] request = args.getString("request").getBytes(StandardCharsets.US_ASCII);
        byte[] expected = args.getString("reply").getBytes(StandardCharsets.US_ASCII);
        for (Network network : manager.getAllNetworks()) {
            NetworkCapabilities caps = manager.getNetworkCapabilities(network);
            LinkProperties link = manager.getLinkProperties(network);
            if (caps == null || !caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
                    || link == null || !"wlan0".equals(link.getInterfaceName())) continue;
            try (Socket socket = network.getSocketFactory().createSocket()) {
                socket.connect(new InetSocketAddress("10.0.2.2", Integer.parseInt(args.getString("port"))), 5000);
                socket.setSoTimeout(5000);
                boolean onInterface = link.getLinkAddresses().stream()
                        .anyMatch(addr -> addr.getAddress().equals(socket.getLocalAddress()));
                if (!onInterface) throw new IOException("source address is not on wlan0");
                socket.getOutputStream().write(request);
                byte[] received = new byte[expected.length];
                new DataInputStream(socket.getInputStream()).readFully(received);
                if (!Arrays.equals(expected, received)) throw new IOException("Wi-Fi reply mismatch");
                return socket.getLocalAddress().getHostAddress();
            }
        }
        throw new IOException("no Wi-Fi Network on wlan0");
    }
}
