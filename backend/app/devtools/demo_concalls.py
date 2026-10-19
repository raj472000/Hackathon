"""DEMO-ONLY synthetic earnings-call transcripts for a fictional company.

They exist so the concall analyzer can be exercised without real transcripts. The company, people
and figures are invented.
"""

SYMBOL = "DEMOIT01"
SOURCE = "Synthetic demo transcript (fictional company)"

Q1 = """Demo Information Technology 01 Limited - Q1 FY2027 Earnings Conference Call (SYNTHETIC DEMO TRANSCRIPT)

Moderator: Good evening and welcome to the Q1 FY2027 earnings call of Demo Information Technology 01 Limited. This transcript is synthetic and for demonstration only.

Chief Executive Officer: Thank you. Revenue for the quarter grew 9 percent year on year. We expect revenue growth for FY2027 to be in the range of 12 to 14 percent, and that guidance remains unchanged. We plan to commission the new Pune delivery centre by the end of Q2 FY2027, adding 2,000 seats. We will launch the Atlas analytics platform for banking clients by Q2 FY2027. We are targeting an EBITDA margin of 24 percent for the full year, up from 22.5 percent last year.

Chief Financial Officer: On the balance sheet, we intend to reduce gross debt by 150 crore by the end of Q2 FY2027 using operating cash flow. Our capex guidance for the year is 400 crore and we will stay within that envelope. We expect to close the acquisition of the cloud consulting business in Europe by September. We aim to bring attrition below 15 percent by next quarter.

Analyst: Can you comment on deal wins?

Chief Executive Officer: The order book is healthy. We are confident of signing at least three large deals above 50 million dollars in the next quarter.

Moderator: Thank you. That concludes the call.
"""

Q2 = """Demo Information Technology 01 Limited - Q2 FY2027 Earnings Conference Call (SYNTHETIC DEMO TRANSCRIPT)

Moderator: Good evening and welcome to the Q2 FY2027 earnings call of Demo Information Technology 01 Limited. This transcript is synthetic and for demonstration only.

Chief Executive Officer: Thank you. Revenue for the quarter grew 13 percent year on year, and revenue growth for FY2027 is tracking in line with our guidance of 12 to 14 percent. The new Pune delivery centre was commissioned in August and the 2,000 seats are now operational. The launch of the Atlas analytics platform for banking clients has been delayed to Q4 FY2027 because certification took longer than planned. EBITDA margin for the first half was 22.8 percent; we remain confident of the full year EBITDA margin target and work is underway on utilisation.

Chief Financial Officer: We have reduced gross debt by 150 crore during the quarter using operating cash flow, as we had said. Capex for the first half was 190 crore, on track against the capex guidance of 400 crore for the year. Attrition was 16.5 percent; we fell short of the attrition level we wanted and could not bring attrition below 15 percent this quarter.

Analyst: And large deals?

Chief Executive Officer: We signed two large deals above 50 million dollars in the quarter.

Moderator: Thank you. That concludes the call.
"""

TRANSCRIPTS = [(2027, 1, Q1), (2027, 2, Q2)]
