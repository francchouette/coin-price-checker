#!/bin/bash
# ==============================================================
# WireGuard VPN サーバーセットアップスクリプト
# Oracle Cloud Ubuntu 22.04 LTS 向け
# 使い方: bash setup-wireguard-server.sh
# ==============================================================

set -e

echo "=================================================="
echo "WireGuard VPN サーバーセットアップ"
echo "=================================================="

# --- 1. WireGuard インストール ---
echo ""
echo "[1/6] WireGuard をインストール中..."
sudo apt-get update -qq
sudo apt-get install -y wireguard wireguard-tools qrencode

# --- 2. 鍵ペア生成 ---
echo ""
echo "[2/6] サーバー鍵ペアを生成中..."
SERVER_PRIVATE_KEY=$(wg genkey)
SERVER_PUBLIC_KEY=$(echo "$SERVER_PRIVATE_KEY" | wg pubkey)

CLIENT_PRIVATE_KEY=$(wg genkey)
CLIENT_PUBLIC_KEY=$(echo "$CLIENT_PRIVATE_KEY" | wg pubkey)
CLIENT_PSK=$(wg genpsk)

echo "  サーバー公開鍵: $SERVER_PUBLIC_KEY"
echo "  クライアント公開鍵: $CLIENT_PUBLIC_KEY"

# --- 3. パブリックIPを取得 ---
echo ""
echo "[3/6] サーバーのパブリックIPを取得中..."
SERVER_IP=$(curl -s ifconfig.me)
echo "  サーバーIP: $SERVER_IP"

# --- 4. サーバー設定ファイル生成 ---
echo ""
echo "[4/6] サーバー設定ファイルを生成中..."
sudo tee /etc/wireguard/wg0.conf > /dev/null << EOF
[Interface]
PrivateKey = $SERVER_PRIVATE_KEY
Address = 10.0.0.1/24
ListenPort = 51820
PostUp   = iptables -A FORWARD -i wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o ens3 -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o ens3 -j MASQUERADE

[Peer]
PublicKey = $CLIENT_PUBLIC_KEY
PresharedKey = $CLIENT_PSK
AllowedIPs = 10.0.0.2/32
EOF

# Oracle Cloud のNIC名はens3/ens3以外の場合があるため確認
NIC=$(ip route | grep default | awk '{print $5}' | head -1)
sudo sed -i "s/ens3/$NIC/g" /etc/wireguard/wg0.conf

# --- 5. IPフォワーディング有効化 ---
echo ""
echo "[5/6] IPフォワーディングを有効化中..."
sudo sysctl -w net.ipv4.ip_forward=1 > /dev/null
echo "net.ipv4.ip_forward = 1" | sudo tee -a /etc/sysctl.conf > /dev/null

# --- 6. WireGuard 起動 ---
echo ""
echo "[6/6] WireGuard を起動中..."
sudo systemctl enable wg-quick@wg0
sudo systemctl start wg-quick@wg0
sudo systemctl status wg-quick@wg0 --no-pager | head -5

# --- クライアント設定ファイル生成 ---
echo ""
echo "=================================================="
echo "Mac用 クライアント設定ファイルを生成しました"
echo "=================================================="

CLIENT_CONF="[Interface]
PrivateKey = $CLIENT_PRIVATE_KEY
Address = 10.0.0.2/32
DNS = 1.1.1.1

[Peer]
PublicKey = $SERVER_PUBLIC_KEY
PresharedKey = $CLIENT_PSK
Endpoint = $SERVER_IP:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25"

echo "$CLIENT_CONF" > ~/wg-client.conf
echo ""
echo "クライアント設定ファイル: ~/wg-client.conf"
echo ""
cat ~/wg-client.conf
echo ""

# QRコード表示（スマホ向け）
echo "=================================================="
echo "QRコード（WireGuard アプリで読み込み可）:"
echo "=================================================="
echo "$CLIENT_CONF" | qrencode -t ansiutf8

echo ""
echo "=================================================="
echo "セットアップ完了！"
echo ""
echo "次のステップ（Mac側）:"
echo "  1. 上記の設定内容を ~/wg-client.conf としてMacに保存"
echo "  2. brew install wireguard-tools  または  App Store で WireGuard をインストール"
echo "  3. WireGuard アプリで設定ファイルをインポート"
echo "  4. 接続ボタンを押す"
echo ""
echo "Oracle Cloud ファイアウォール設定（必須）:"
echo "  - ポート 51820/UDP を許可"
echo "=================================================="
