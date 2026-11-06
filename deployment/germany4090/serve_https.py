import socket
import uvicorn

if __name__ == "__main__":
    listener = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    listener.bind(("::", 18443))
    listener.listen(2048)
    config = uvicorn.Config(
        "public_api_proxy:app",
        ssl_certfile="/etc/letsencrypt/live/cloudpath-vps-ip/fullchain.pem",
        ssl_keyfile="/etc/letsencrypt/live/cloudpath-vps-ip/privkey.pem",
        proxy_headers=False,
    )
    uvicorn.Server(config).run(sockets=[listener])
