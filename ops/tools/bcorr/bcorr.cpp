/**
 * Gsim Tools -- Bcorr
 * 
 * Author: wbai
 * E-mail: wenbo@graceim.ai
 */
#include <cmath>
#include <cstddef>
#include <cstdlib>
#include <utility>
#include <iostream>
#include <fstream>
#include <future>
#include <exception>
#include <filesystem>
#include <string>
#include <vector>
#include <unordered_map>
#include <algorithm>

namespace fs = std::filesystem;

void parseOptions(int argc, char* argv[],
        std::string& startDate,
        std::string& endDate,
        std::string& fileName,
        std::string& pathName) {
    if (argc < 3) {
        throw std::invalid_argument("Usage: ./bcorr <file> <path> [-s start_date] [-e end_date]");
    }
    
    startDate = "20190701";
    endDate = "20221231";

    fileName = argv[1];
    pathName = argv[2];

    for (int i = 3; i < argc; ++i) {
        std::string arg { argv[i] };
        if (arg =="-h") {
            std::cout << "-s: start date" << '\n'
                      << "-e: end date" << '\n'
                      << "-f: file" << '\n'
                      << "-p: path" << std::endl;
            exit(0);
        }
        else if (arg == "-s" && i + 1 < argc) {
            startDate = argv[++i];
        }
        else if (arg == "-e" && i + 1 < argc) {
            endDate = argv[++i];
        }
        else if (arg == "-f" && i + 1 < argc) {
            fileName = argv[++i];
        }
        else if (arg == "-p" && i + 1 < argc) {
            pathName = argv[++i];
        }
    }
}

int dateToInt(const std::string& dateStr) {
    if (dateStr.length() != 8) {
        return 0;
    }
    try {
        return std::stoi(dateStr);
    }
    catch (...) {
        return 0;
    }
}

// 读取单个文件的数据
std::unordered_map<int, double>
        getDailyReturnEach(const fs::path& fileName) {
    std::unordered_map<int, double> data;
    std::ifstream file(fileName);

    if (!file.is_open()) {
        return data;
    }

    std::string line;
    while (std::getline(file, line)) {
        if (line.empty()) {
            continue;
        }

        std::istringstream iss(line);

        std::string col;
        int col_index = 0;

        // 保留第 0 列和第 4 列
        int date = 0;       // 日期
        double val = 0.0;   // 计算相关性需要的数据
        while (iss >> col) {
            if (col_index == 0) {
                date = std::stoi(col);
            }
            else if (col_index == 4) {
                val = std::stod(col);
            }
            ++col_index;
        }
        data[date] = val;
    }
    return data;
}

// 获取指定日期范围的共同数据
std::pair<std::vector<double>, std::vector<double>> getCommonData(
        const std::unordered_map<int, double>& data1,
        const std::unordered_map<int, double>& data2,
        int start,
        int end) {
    std::pair<std::vector<double>, std::vector<double>> result;
    for (const auto& [date, val1]: data1) {
        if (date > start && date < end) {
            auto it = data2.find(date);
            if (it != data2.end()) {
                result.first.emplace_back(val1);
                result.second.emplace_back(it->second);
            }
        }
    }
    return result;
}

// 计算相关性
double calculateCorrelation(
        const std::vector<double>& x,
        const std::vector<double>& y) {
    if (x.size() != y.size() || x.size() < 2) {
        return std::nan("");
    }

    double sumX = 0, sumY = 0, sumXY = 0;
    double sumX2 = 0, sumY2 = 0;
    
    std::size_t n = x.size();
    for (std::size_t i = 0; i < n; ++i) {
        sumX += x[i];
        sumY += y[i];
        sumXY += x[i] * y[i];
        sumX2 += x[i] * x[i];
        sumY2 += y[i] * y[i];
    }

    double numerator = n * sumXY - sumX * sumY;
    double denominator = std::sqrt((n * sumX2 - sumX * sumX) * (n * sumY2 - sumY * sumY));

    if (denominator == 0) {
        return std::nan("");
    }
    return numerator / denominator;
}

std::vector<std::pair<fs::path, double>> bcorr(
            const fs::path& fileName,
            const fs::path& pathName,
            int start,
            int end) {

    std::unordered_map<fs::path, std::unordered_map<int, double>> allData;

    // 处理 name2 是目录的情况
    if (fs::is_directory(pathName)) {

        // 展开目录
        std::vector<fs::path> files;
        files.push_back(fileName);

        // TODO: 添加 pnl 文件时, 仅忽略扩展名, 文件夹下不能有其它没有扩展名的文件
        auto traverseDirectory = [&](auto && self, const fs::path& dirName) -> void {
            for (const auto& entry: fs::directory_iterator(dirName)) {
                if (entry.is_directory()) {
                    self(self, entry);
                }
                else if (entry.is_regular_file()
                            && entry != fileName
                            && !entry.path().has_extension()) {
                    files.push_back(entry);
                }
            }
        };

        // 递归遍历目录
        traverseDirectory(traverseDirectory, pathName);

        // 分批次处理, 每批处理 10 个
        const size_t BATCH_SIZE = 10;

        // 提取每个文件的第 0 行和第 4 行
        for (size_t i = 0; i < files.size(); i += BATCH_SIZE) {
            std::vector<std::future<
                std::pair<fs::path, std::unordered_map<int, double>>>>
                    batch_futures;
            
            for (size_t j = i; j < std::min(i + BATCH_SIZE, files.size()); ++j) {
                batch_futures.emplace_back(std::async(std::launch::async,
                        [file = files[j]]() {
                    return std::make_pair(file, getDailyReturnEach(file));
                }));
            }

            // 等待获取结果
            for (auto& f: batch_futures) {
                auto [file, data] = f.get();
                if (!data.empty()) {
                    allData[file] = data;
                }
            }
        }
    }
    // 处理 name2 是文件的情况
    else {
        if (fileName == pathName) {
            // std::cerr << fileName.string() << ' ' << 1.0 << std::endl;
            return { std::make_pair(fileName.string(), 1.0) };
        }

        auto data1 = getDailyReturnEach(fileName);
        auto data2 = getDailyReturnEach(pathName);

        if (!data1.empty() && !data2.empty()) {
            allData[fileName] = data1;
            allData[pathName] = data2;
        }
    }

    if (allData.find(fileName) == allData.end()) {
        throw std::runtime_error("Can't read data from file.");
    }

    // 计算相关性并排序
    std::vector<std::pair<fs::path, double>> corrResults;
    for (const auto& [name, data]: allData) {
        if (name == fileName) {
            continue;
        }

        auto [x, y]
            = getCommonData(allData[fileName], data, start, end);

        double corr = calculateCorrelation(x, y);
        if (!std::isnan(corr)) {
            corrResults.emplace_back(name, corr);
        }
    }

    std::sort(corrResults.begin(), corrResults.end(),
        [](const auto& a, const auto& b) {
        return a.second < b.second;
    });

    return corrResults;
}

int main(int argc, char* argv[]) {
    try {
        std::string startDate, endDate, fileName, pathName;
        parseOptions(argc, argv, startDate, endDate, fileName, pathName);

        int start = dateToInt(startDate);
        int end = dateToInt(endDate);

        if (start == 0 || end == 0) {
            throw std::invalid_argument("Invalid format, must be YYYYMMDD");
        }

        auto results = bcorr(fileName, pathName, start, end);

        for (const auto& [name, corr]: results) {
            std::cout << name.filename().string() << ' '
                        << std::fixed << std::setprecision(5)
                        << corr << '\n';
        }
    }
    catch (const std::exception& e) {
        std::cerr << "Error: " << e.what() << std::endl;
        return 1;
    }
    return 0;
}
