% ical                                                                2-Aug-94
function ical()     % calibrates input channels

Npts = 2048;
TossGn  = 1;
TossD   = 1;
TossA   = 2;
TossOff = 1;
TestV = 2.0;
FC = 3.14159;           % special code for writing eerom factory cal
clc;
[drive,ppath]=pathfind('vbin');   
[nIn,nOut] = siglab('IOinit',[drive,ppath,'\siglab.out']);
pause(1);
if nIn == 0 disp('No input channels found, check power switch');
else
  fprintf(1,'Enter channel number to calibrate\n');
  fprintf(1,'or press ENTER to calibrate all input channels (1 to %d)\n',nIn);
  k = input(' ---> ');
  if length(k) == 0   chA = 1;  chB = nIn;  else  chA = k;  chB = k;  end;
  if nOut == 0
    fprintf(1,'Set an external DC source (or battery) to anywhere\n');
    fprintf(1,'between 1.4 and 2.2 volts.\n');
    vcal = input('Enter this DC value in volts: ');
    fprintf(1,'Connect this source input channels %d through %d\n',chA,chB);
  else  
    siglab('SendArb',ones(1,100),1);
    siglab('OutBurst',1,0,0,'Arb',1);
    siglab('OutLevel',1,TestV);
    fprintf(1,'Measure output channel 1 with a DC Voltmeter.\n');
    vcal = input('Enter this DC value in volts: ');
    fprintf(1,'Connect output channel 1 to input channels %d through %d\n',chA,chB);
  end;    
  chans = chA : chB;
  n = length(chans);    % number of channels to calibrate
  igain = ones(10,n);    % reset cal factors
  ioff = zeros(10,n);
  idac = [ones(2,n); zeros(2,n)];
  v1 = zeros(6,n);  v0 = v1;
  siglab('InpSet',chans,Npts,'BW',2000);
  for ch = chans
    siglab('Trigger',ch,'FreeRun');
    siglab('SendCal','Chan',ch,...
           'Igain',igain(:,1),'Ioff',ioff(:,1),'Idac',idac(:,1));
  end;
  input('Press Enter when ready: ');
  fprintf(1,' Calibrating . . . \n');
  
  for k=1:6
    if k<4 siglab('InpGain',chans,20/2^k);
    else siglab('InpGain',chans,2.5,'cal',k+6);
    end;
    v1(k,:) = vavg(chans,Npts,TossGn);
  end;  
  fprintf(1,'Remove the signal and short out channels %d through %d %c%c%c\n', ...
             chA,chB,7,7,7);
  input('Press enter when ready');
  fprintf(1,' Calibrating . . . \n');
  for k=1:6
    if k<4 siglab('InpGain',chans,20/2^k);
    else siglab('InpGain',chans,2.5,'cal',k+6);
    end;
    v0(k,:) = vavg(chans,Npts,TossGn);
  end;  
  c = vcal ./ (v1 - v0); % gain corrections for each of the six gain ranges
  g1 = c(4,:) ./ c(1,:); % gain corrections for gain stage 1 (12 dB)
  g2 = c(5,:) ./ c(1,:); % gain corrections for gain stage 2 (12 dB)
  g3 = c(6,:) ./ c(1,:); % gain corrections for gain stage 3 (12 dB)
  g4 = c(2,:) ./ c(1,:); % gain corrections for gain stage 4 (6 dB)
  igain = [c(1,:); ...                        % 10 V
           c(2,:); ...                        % 5 V
           c(3,:); ...                        % 2.5 V
           c(3,:) .* g4; ...                  % 1.25 V
           c(3,:) .* g1; ...                  % 625 mV
           c(3,:) .* g1 .* g4; ...            % 312 mV
           c(3,:) .* g1 .* g2; ...            % 156 mV
           c(3,:) .* g1 .* g2 .* g4; ...      % 78 mV
           c(3,:) .* g1 .* g2 .* g3; ...      % 39 mV
           c(3,:) .* g1 .* g2 .* g3 .* g4];   % 20 mV
  for ch = chans  siglab('SendCal','Chan',ch,'Igain',igain(:,ch-chA+1)); end;
  
  % now calibrate input offset dac
  
  vrange = [10 2.5];  % voltage ranges used for input dac cal
  vt = .8 * vrange;   % test voltage = 80% of input range
  for k = 1:2         % do dac gain cal for 10 and 2.5 volt ranges
    siglab('InpGain',chans,vrange(k));
    va = vavg(chans,Npts,TossD);
    siglab('InpGain',chans,vrange(k),'Offset',vt(k));
    idac(k,:) = vt(k) ./ (vavg(chans,Npts,TossD) - va);
  end;       
  siglab('InpGain',chans,0.156);        % dac offset cal (DC)
  idac(3,:) = -vavg(chans,Npts,TossD);
  siglab('InpGain',chans,0.156,'AC');   % dac offset cal (AC)
  idac(4,:) = -vavg(chans,Npts,TossA);
  for ch = chans  siglab('SendCal','Chan',ch,'Idac',idac(:,ch-chA+1)); end;

  % now do final offset calibration
  vr = 10;
  for k=1:10
    siglab('InpGain',chans,vr);
    ioff(k,:) = -vavg(chans,Npts,TossOff);
    vr = vr/2;
  end;  
  for ch = chans  siglab('SendCal','Chan',ch,'Ioff',ioff(:,ch-chA+1)); end;

  f = 1;        % send table of results to command window
  fprintf(f,'\nVrange    ');
  for ch=chans fprintf(f,'Ch%d(gain)   ',ch); end;
  for ch=chans fprintf(f,'Ch%d(uV off) ',ch); end;
  fprintf(f,'\n');  vr = 10;
  for k = 1:10
    fprintf(f,'%6.3f     ',vr);
    for j=1:n fprintf(f,'%7.5f     ',igain(k,j));    end;
    for j=1:n fprintf(f,'%7.0f     ',1e6*ioff(k,j)); end;
    fprintf(f,'\n');  vr = vr/2;
  end;
  fprintf(f,'\nOFFSET DAC:    ');
  for ch=chans fprintf(f,'Chan%d      ',ch); end;
  fprintf(f,'\ngain @ 10V    ');
      for j=1:n fprintf(f,'%7.5f    ',idac(1,j)); end;
  fprintf(f,'\ngain @ 2.5V   ');
      for j=1:n fprintf(f,'%7.5f    ',idac(2,j)); end;
  fprintf(f,'\nmV off (DC)   ');
      for j=1:n fprintf(f,'%7.2f    ',1000*idac(3,j)); end;
  fprintf(f,'\nmV off (AC)   ');
      for j=1:n fprintf(f,'%7.2f    ',1000*idac(4,j)); end;

  if max([max(max(abs(ioff))), max(abs(idac(3,:))), max(abs(idac(4,:)))]) > 0.3
    fprintf(f,'\n ********* Error: Input offset greater than 300 mV');
  end;  
  cf(18,:) = ones(1,n);
  if max([max(max(abs(1-igain))), ...
          max(abs(1-idac(1,:))), ...
          max(abs(1-idac(2,:)))]) > 0.3
    fprintf(f,'\n ********* Error: Gain error greater than 30 percent');
  end;

  sv = 0;
  while sv == 0 
    fprintf(1,'\n\n Enter  1  to save above values to EEROM\n');
    fprintf(1,' 2  to restore the factory calibration factors\n');
    fprintf(1,' 3  to save uncalibrated factors to EEROM\n');
    fprintf(1,' 4  to skip writing of the EEROM\n');
    sv = input(' ---> ');
    if sv == 1       siglab('SendCal','SaveI');    disp('user cal written');
    elseif sv == 2   siglab('SendCal','RestoreI'); disp('factory cal restored');
    elseif sv == FC  siglab('SendCal','FactoryI');
                     siglab('SendCal','SaveI');
                     disp('factory and user cals written');
    elseif sv == 3   for ch = chans 
                       siglab('SendCal','Chan',ch,'Igain', ones(10,1),...
                              'Ioff',zeros(10,1),'Idac',[1 1 0 0]);
                     end;
                     siglab('SendCal','SaveI'); disp('uncalibrated');
    elseif sv == 4   disp('EEROM not written');
    else sv = 0;     % indicate that no legal choice was entered
    end;             % end if sv == 1
  end;               % end while sv == 0
end;                 % end if nIn == 0 
% end function ical












