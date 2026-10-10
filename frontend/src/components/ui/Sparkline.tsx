interface SparklineProps {
  data?: number[]
  color?: string
  width?: number
  height?: number
  className?: string
}

export function Sparkline({
  data = [35, 45, 40, 60, 55, 75, 70, 85],
  color = '#34d399',
  width = 72,
  height = 24,
  className = '',
}: SparklineProps) {
  const points = data.length > 0 ? data : [50, 50]
  const min = Math.min(...points)
  const max = Math.max(...points)
  const range = max - min || 1

  const padding = 2
  const effectiveWidth = width - padding * 2
  const effectiveHeight = height - padding * 2

  const coords = points.map((val, index) => {
    const x = padding + (index / (points.length - 1)) * effectiveWidth
    const y = padding + effectiveHeight - ((val - min) / range) * effectiveHeight
    return { x, y }
  })

  // Generar curva Bezier suave
  let pathD = `M ${coords[0].x} ${coords[0].y}`
  for (let i = 0; i < coords.length - 1; i++) {
    const curr = coords[i]
    const next = coords[i + 1]
    const cx = (curr.x + next.x) / 2
    pathD += ` C ${cx} ${curr.y}, ${cx} ${next.y}, ${next.x} ${next.y}`
  }

  const lastPoint = coords[coords.length - 1]

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={`overflow-visible ${className}`}
      aria-hidden="true"
    >
      <defs>
        <filter id={`glow-${color.replace('#', '')}`} x="-20%" y="-20%" width="140%" height="140%">
          <feDropShadow dx="0" dy="0" stdDeviation="1.5" floodColor={color} floodOpacity="0.6" />
        </filter>
      </defs>
      <path
        d={pathD}
        fill="none"
        stroke={color}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{ filter: `drop-shadow(0 0 3px ${color}88)` }}
      />
      {lastPoint && (
        <circle
          cx={lastPoint.x}
          cy={lastPoint.y}
          r="2.5"
          fill={color}
          style={{ filter: `drop-shadow(0 0 4px ${color})` }}
        />
      )}
    </svg>
  )
}
