interface SparklineProps {
  data?: number[]
  color?: string
  width?: number
  height?: number
  className?: string
}

export function Sparkline({
  data = [35, 45, 40, 60, 55, 75, 70, 85],
  color = '#0a84ff',
  width = 68,
  height = 20,
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

  // Generar curva Bezier limpia
  let pathD = `M ${coords[0].x.toFixed(1)} ${coords[0].y.toFixed(1)}`
  for (let i = 0; i < coords.length - 1; i++) {
    const curr = coords[i]
    const next = coords[i + 1]
    const cx = ((curr.x + next.x) / 2).toFixed(1)
    pathD += ` C ${cx} ${curr.y.toFixed(1)}, ${cx} ${next.y.toFixed(1)}, ${next.x.toFixed(1)} ${next.y.toFixed(1)}`
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
      <path
        d={pathD}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {lastPoint && (
        <circle
          cx={lastPoint.x}
          cy={lastPoint.y}
          r="2"
          fill={color}
        />
      )}
    </svg>
  )
}
