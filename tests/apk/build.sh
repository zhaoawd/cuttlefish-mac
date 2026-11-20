#!/bin/bash
# 在测试临时目录中生成 APK 和两把临时签名密钥。
set -eu
src=$(cd "$(dirname "$0")" && pwd)
cd "$1"
sdk=${ANDROID_HOME:-"$HOME/Library/Android/sdk"}
tools="$sdk/build-tools/${CF_BUILD_TOOLS:-36.1.0}"
android="$sdk/platforms/${CF_PLATFORM:-android-36.1}/android.jar"
if [[ -z "${JAVA_HOME:-}" && -d "/Applications/Android Studio.app/Contents/jbr/Contents/Home" ]]; then
  export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
fi
export PATH="${JAVA_HOME:+$JAVA_HOME/bin:}$PATH"
mkdir -p classes dex
javac --release 8 -cp "$android" -d classes "$src/Main.java"
"$tools/d8" --min-api 28 --lib "$android" --output dex classes/dev/cuttlefish/cfctl/fixture/Main.class
for key in primary secondary; do
  keytool -genkeypair -alias dev -keystore "$key.p12" -storepass testonly -keypass testonly \
    -keyalg RSA -dname CN=cfctl-test -validity 10 >/dev/null
done
for version in 1 2; do
  "$tools/aapt2" link --manifest "$src/AndroidManifest.xml" -I "$android" \
    --version-code "$version" --version-name "$version" -o "raw$version.apk"
  (cd dex && zip -q "../raw$version.apk" classes.dex)
  "$tools/zipalign" -f 4 "raw$version.apk" "aligned$version.apk"
  "$tools/apksigner" sign --ks primary.p12 --ks-pass pass:testonly --out "v$version.apk" "aligned$version.apk"
done
"$tools/apksigner" sign --ks secondary.p12 --ks-pass pass:testonly --out other.apk aligned2.apk
cat > split.xml <<'XML'
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="dev.cuttlefish.cfctl.fixture" split="config.en">
    <uses-sdk android:minSdkVersion="28" android:targetSdkVersion="35"/>
    <application android:hasCode="false"/>
</manifest>
XML
"$tools/aapt2" link --manifest split.xml -I "$android" --version-code 2 -o split-raw.apk
"$tools/zipalign" -f 4 split-raw.apk split-aligned.apk
"$tools/apksigner" sign --ks primary.p12 --ks-pass pass:testonly --out split.apk split-aligned.apk
