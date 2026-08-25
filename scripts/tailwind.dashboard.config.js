module.exports = {
  content: [
    "./src/janus/dashboard/templates/**/*.html",
    "./src/janus/dashboard/static/js/**/*.js",
  ],
  safelist: [
    "bg-green-900",
    "text-green-200",
    "bg-red-900",
    "text-red-200",
    "bg-yellow-900",
    "text-yellow-200",
    "bg-orange-900",
    "text-orange-200",
    "bg-purple-900",
    "text-purple-200",
    "bg-gray-700",
    "text-gray-200",
  ],
  theme: { extend: {} },
  plugins: [],
};
