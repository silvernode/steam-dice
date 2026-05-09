# Maintainer: Mollusk <silvernode@gmail.com>
pkgname=steam-dice-git
_pkgname=steam-dice
pkgver=r30.4ba3aa0
pkgrel=1
pkgdesc="A PyQt6 desktop app that picks a random game from your Steam library"
arch=('any')
url="https://github.com/silvernode/steam-dice"
license=('GPL-2.0-only')
depends=('python' 'python-pyqt6' 'python-requests' 'python-keyring' 'xdg-utils')
optdepends=('python-steam: instant genre + tag filters via Steam'\''s local appinfo.vdf cache (without it, genres fall back to the rate-limited Steam API and tag filtering is unavailable)')
makedepends=('git')
provides=("$_pkgname")
conflicts=("$_pkgname")
source=("$_pkgname::git+https://github.com/silvernode/steam-dice.git")
sha256sums=('SKIP')

pkgver() {
    cd "$_pkgname"
    printf "r%s.%s" "$(git rev-list --count HEAD)" "$(git rev-parse --short HEAD)"
}

package() {
    cd "$_pkgname"
    install -Dm755 steam_dice.py "$pkgdir/usr/bin/steam-dice"
    install -Dm644 io.github.silvernode.SteamDice.desktop \
        "$pkgdir/usr/share/applications/io.github.silvernode.SteamDice.desktop"
    install -Dm644 io.github.silvernode.SteamDice.svg \
        "$pkgdir/usr/share/icons/hicolor/scalable/apps/io.github.silvernode.SteamDice.svg"
    install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$_pkgname/LICENSE"
    install -Dm644 README.md "$pkgdir/usr/share/doc/$_pkgname/README.md"
}
