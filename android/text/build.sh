#!/bin/bash
# 生成仅供本机验证的 instrumentation APK，签名密钥保存在输出目录。
set -euo pipefail
src=$(cd "$(dirname "$0")" && pwd)
out=${1:-"$src/../../.local-cf/text-helper"}
mkdir -p "$out"
cd "$out"
sdk=${ANDROID_HOME:-"$HOME/Library/Android/sdk"}
tools="$sdk/build-tools/${CF_BUILD_TOOLS:-36.1.0}"
android="$sdk/platforms/${CF_PLATFORM:-android-36.1}/android.jar"
if [[ -z "${JAVA_HOME:-}" && -d "/Applications/Android Studio.app/Contents/jbr/Contents/Home" ]]; then
  export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
fi
export PATH="${JAVA_HOME:+$JAVA_HOME/bin:}$PATH"
work=$(mktemp -d "$PWD/build.XXXXXX")
trap 'rm -rf "$work"' EXIT
mkdir "$work/classes" "$work/dex"
javac --release 8 -cp "$android" -d "$work/classes" "$src/SetText.java"
"$tools/d8" --min-api 28 --lib "$android" --output "$work/dex" "$work/classes/dev/cuttlefish/cfctl/text/SetText.class"
"$tools/aapt2" link --manifest "$src/AndroidManifest.xml" -I "$android" --version-code 1 --version-name 1 -o "$work/raw.apk"
(cd "$work/dex" && zip -q ../raw.apk classes.dex)
"$tools/zipalign" -f 4 "$work/raw.apk" "$work/aligned.apk"
if [[ ! -e text.p12 ]]; then
  (umask 077; keytool -genkeypair -alias dev -keystore "$work/text.p12" -storepass localdev -keypass localdev \
    -keyalg RSA -dname CN=cfctl-local -validity 3650 && ln "$work/text.p12" text.p12) || [[ -s text.p12 ]]
fi
"$tools/apksigner" sign --ks text.p12 --ks-pass pass:localdev --out "$work/text.apk" "$work/aligned.apk"
mv "$work/text.apk" text.apk
