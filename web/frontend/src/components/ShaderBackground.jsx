import { useEffect, useRef } from 'react'

// Exact WebGL background from the Precog design: radiating concentric cyan
// "interference" lines, animated. The fragment shader is ported verbatim from
// the source so the hero looks identical. `intensity` lets us reuse the same
// field, dimmed, behind the comparison view without it competing with content.
const VERT = 'attribute vec2 pos; void main(){ gl_Position = vec4(pos, 0.0, 1.0); }'

const FRAG = [
  'precision highp float;',
  'uniform vec2 u_res; uniform float u_time; uniform float u_intensity;',
  'void main(void){',
  '  vec2 uv = (gl_FragCoord.xy * 2.0 - u_res.xy) / min(u_res.x, u_res.y);',
  '  float t = u_time * 0.14;',
  '  float lineWidth = 0.0022;',
  '  vec3 acc = vec3(0.0);',
  '  for(int j = 0; j < 3; j++){',
  '    for(int i = 0; i < 5; i++){',
  '      acc[j] += lineWidth * float(i*i) / abs(fract(t - 0.01*float(j) + float(i)*0.01) * 5.0 - length(uv) + mod(uv.x + uv.y, 0.2));',
  '    }',
  '  }',
  // Collapse the accumulated brightness, then tint it a light bold orange so
  // the whole field reads warm instead of the original cyan.
  '  float lum = (acc.r + acc.g + acc.b);',
  '  vec3 orange = vec3(1.0, 0.60, 0.22);',
  '  gl_FragColor = vec4(lum * orange * u_intensity, 1.0);',
  '}',
].join('\n')

export default function ShaderBackground({
  intensity = 1,
  className = '',
  style = {},
  frozen = false,      // render a single fixed frame, no animation loop
  frozenTime = 8.0,    // which frame (seconds) to freeze on
}) {
  const ref = useRef(null)

  useEffect(() => {
    const cv = ref.current
    if (!cv) return
    const gl = cv.getContext('webgl', {
      antialias: true,
      alpha: false,
      premultipliedAlpha: false,
    })
    if (!gl) return

    const compile = (type, src) => {
      const s = gl.createShader(type)
      gl.shaderSource(s, src)
      gl.compileShader(s)
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
        console.warn(gl.getShaderInfoLog(s))
      }
      return s
    }
    const prog = gl.createProgram()
    gl.attachShader(prog, compile(gl.VERTEX_SHADER, VERT))
    gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, FRAG))
    gl.linkProgram(prog)

    const buf = gl.createBuffer()
    gl.bindBuffer(gl.ARRAY_BUFFER, buf)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW)
    const loc = gl.getAttribLocation(prog, 'pos')
    const uRes = gl.getUniformLocation(prog, 'u_res')
    const uTime = gl.getUniformLocation(prog, 'u_time')
    const uIntensity = gl.getUniformLocation(prog, 'u_intensity')

    const dpr = Math.min(window.devicePixelRatio || 1, 1.75)
    const resize = () => {
      const w = Math.max(1, Math.floor(cv.clientWidth * dpr))
      const h = Math.max(1, Math.floor(cv.clientHeight * dpr))
      if (cv.width !== w || cv.height !== h) {
        cv.width = w
        cv.height = h
      }
    }
    const draw = (t) => {
      gl.viewport(0, 0, cv.width, cv.height)
      gl.useProgram(prog)
      gl.bindBuffer(gl.ARRAY_BUFFER, buf)
      gl.enableVertexAttribArray(loc)
      gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0)
      gl.uniform2f(uRes, cv.width, cv.height)
      gl.uniform1f(uTime, t)
      gl.uniform1f(uIntensity, intensity)
      gl.drawArrays(gl.TRIANGLES, 0, 3)
    }

    window.addEventListener('resize', resize)
    resize()

    // Frozen: draw one fixed frame and only redraw on resize. No rAF loop.
    if (frozen) {
      const drawFrozen = () => {
        resize()
        draw(frozenTime)
      }
      drawFrozen()
      window.addEventListener('resize', drawFrozen)
      return () => {
        window.removeEventListener('resize', resize)
        window.removeEventListener('resize', drawFrozen)
      }
    }

    const start = performance.now()
    let raf
    const loop = () => {
      resize()
      draw((performance.now() - start) / 1000)
      raf = requestAnimationFrame(loop)
    }
    loop()

    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', resize)
    }
  }, [intensity, frozen, frozenTime])

  return (
    <canvas
      ref={ref}
      className={className}
      style={{ display: 'block', ...style }}
    />
  )
}
