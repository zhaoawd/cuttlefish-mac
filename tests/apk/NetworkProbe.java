package dev.cuttlefish.cfctl.fixture;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.LinkProperties;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.os.Bundle;
import android.os.SystemClock;
import java.io.DataInputStream;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URL;
import java.net.UnknownHostException;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import javax.net.ssl.HttpsURLConnection;

final class NetworkProbe {
    static String exchange(Context context, Bundle args) throws IOException {
        ConnectivityManager manager = context.getSystemService(ConnectivityManager.class);
        boolean cellular = "cellular-data".equals(args.getString("test"));
        int transport = cellular ? NetworkCapabilities.TRANSPORT_CELLULAR : NetworkCapabilities.TRANSPORT_WIFI;
        for (Network network : manager.getAllNetworks()) {
            NetworkCapabilities caps = manager.getNetworkCapabilities(network);
            LinkProperties link = manager.getLinkProperties(network);
            if (caps == null || !caps.hasTransport(transport) || link == null) continue;
            String iface = link.getInterfaceName();
            if (!cellular && !"wlan0".equals(iface)) continue;
            if ("wifi-https".equals(args.getString("test"))) {
                return publicHttps(network, iface);
            }
            byte[] request = args.getString("request").getBytes(StandardCharsets.US_ASCII);
            byte[] expected = args.getString("reply").getBytes(StandardCharsets.US_ASCII);
            try (Socket socket = network.getSocketFactory().createSocket()) {
                socket.connect(new InetSocketAddress(args.getString("gateway"), Integer.parseInt(args.getString("port"))), 5000);
                socket.setSoTimeout(10000);
                boolean onInterface = link.getLinkAddresses().stream()
                        .anyMatch(addr -> addr.getAddress().equals(socket.getLocalAddress()));
                if (!onInterface) throw new IOException("unexpected source address on " + iface);
                DataInputStream in = new DataInputStream(socket.getInputStream());
                socket.getOutputStream().write(request);
                byte[] received = new byte[expected.length];
                in.readFully(received);
                if (!Arrays.equals(expected, received)) throw new IOException("reply mismatch");
                byte[] block = new byte[Integer.parseInt(args.getString("bytes"))];
                if (block.length > 0) {
                    socket.getOutputStream().write(1);
                    in.readFully(block);
                    socket.getOutputStream().write(1);
                    if (in.read() != 1) throw new IOException("missing upload start");
                    socket.getOutputStream().write(block);
                    if (in.read() != 1) throw new IOException("host did not verify upload");
                }
                return iface + " " + socket.getLocalAddress().getHostAddress();
            }
        }
        throw new IOException("no connected Network for transport " + transport);
    }

    private static String publicHttps(Network network, String iface) throws IOException {
        URL url = new URL("https://connectivitycheck.gstatic.com/generate_204");
        long until = SystemClock.elapsedRealtime() + 15000;
        while (true) {
            HttpsURLConnection conn = (HttpsURLConnection) network.openConnection(url);
            try {
                conn.setConnectTimeout(7000);
                conn.setReadTimeout(7000);
                conn.setInstanceFollowRedirects(false);
                int status = conn.getResponseCode();
                if (status != 204) throw new IOException("HTTP status " + status);
                return iface + " HTTPS 204";
            } catch (UnknownHostException e) {
                if (SystemClock.elapsedRealtime() >= until) throw e;
                SystemClock.sleep(500);
            } finally {
                conn.disconnect();
            }
        }
    }
}
