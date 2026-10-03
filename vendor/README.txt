qjs.exe - QuickJS-ng standalone interpreter, bundled as the JS runtime.

YouTube requires a JavaScript runtime to solve its player challenges; without
one yt-dlp warns and some formats go missing.  QuickJS is one of the four
runtimes yt-dlp supports (deno, node, bun, quickjs) and by far the smallest:
2 MB against roughly 80 MB for node and 110 MB for deno, which is what makes it
practical to ship inside the installer so a fresh machine needs nothing.

  source  : https://github.com/quickjs-ng/quickjs/releases
  asset   : qjs-windows-x86_64.exe
  version : v0.17.0
  licence : MIT - full text in LICENSE-quickjs-ng.txt, which must ship with it

yt-dlp warns that quickjs older than 0.12.0 solves challenges very slowly, so
stay on 0.12.0 or newer when updating this.  To update, download the asset
again and drop it here as qjs.exe; build.bat picks it up.
